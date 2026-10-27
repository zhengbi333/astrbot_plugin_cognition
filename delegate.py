"""子代理（对标 AstrBot 的 subagent handoff，按我们自己的链路实现）。

设计取舍（为什么这么分）：
- **主代理负责取数，子代理负责思考**。她（主链路）有 31 个工具，能把文件、网页、记忆里的
  材料找来；子代理**不给工具**，只在一个干净的上下文里想清楚再交回结论。
  这样既躲开"子代理再调一轮工具"的复杂度，也避免它乱动工作区。
- **一行描述**：主模型只看到每个专家的一句话（description），要看全部再 list。
- **per-agent provider**：便宜活可以指定便宜模型，这是成本控制的关键。
- **日调用上限**：AstrBot 那套没有，容易花钱失控；这里默认每天 30 次。

和 AstrBot handoff 的差别：
| | AstrBot handoff | 我们 |
|---|---|---|
| 触发 | 注册成工具，主链自动可见 | 显式 `toolbox_delegate(agent=...)` |
| 子代理工具 | 可配工具集 | 不给（主代理取数） |
| 成本护栏 | 无 | 材料/输出上限 + 每日次数 |
| 审计 | 框架日志 | `_note_call` 记录 |
"""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any

MAX_MATERIAL_CHARS = 4000
MAX_OUTPUT_CHARS = 2000
DEFAULT_DAILY_LIMIT = 30

SYSTEM_PROMPT = (
    "你是一个独立的执行者，只负责把交给你的那件小事做完。"
    "不要寒暄、不要解释你在做什么、不要复述任务。"
    "直接给出结果；如果信息不足，就明确指出缺什么，而不是猜。"
)

PROMPT_TEMPLATE = """任务：
{task}

材料：
{material}

要求：直接给结果，不超过 {limit} 字。"""


class DelegateError(Exception):
    """子代理失败（信息面向模型可读）。"""


@dataclass
class Expert:
    """一个「专家」定义。"""

    name: str
    description: str = ""
    system: str = ""
    provider_id: str = ""
    max_chars: int = MAX_OUTPUT_CHARS
    material_limit: int = MAX_MATERIAL_CHARS
    enabled: bool = True

    def one_line(self) -> str:
        return f"- {self.name}：{self.description}" if self.description else f"- {self.name}"


DEFAULT_EXPERT = Expert(
    name="默认",
    description="通用独立推理（无工具、无专长）",
    system=SYSTEM_PROMPT,
)


@dataclass(slots=True)
class DelegateResult:
    text: str
    prompt_chars: int
    output_chars: int
    model: str = ""
    expert: str = ""

    @property
    def header(self) -> str:
        bits = ["子代理"]
        if self.expert:
            bits.append(self.expert)
        if self.model:
            bits.append(self.model)
        return "（" + " · ".join(bits) + "）"


@dataclass
class CallBudget:
    """每天的子代理调用计数（进程内存，重启清零可以接受）。"""

    daily_limit: int = DEFAULT_DAILY_LIMIT
    counts: dict[str, int] = field(default_factory=dict)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def used(self) -> int:
        return int(self.counts.get(self._today(), 0))

    def left(self) -> int:
        return max(0, self.daily_limit - self.used())

    def take(self) -> bool:
        day = self._today()
        if self.used() >= self.daily_limit:
            return False
        self.counts[day] = self.used() + 1
        return True


# ---------------------------------------------------------------- 专家解析
def parse_experts(raw: Any) -> list[Expert]:
    """把配置里的 agents 解析成专家列表（容忍 JSON 字符串、dict、list）。"""
    data = raw
    if isinstance(raw, str):
        text = str(raw).strip()
        if not text:
            return []
        for candidate in (text, text.replace("\\", "/")):
            try:
                data = json.loads(candidate)
                break
            except (ValueError, TypeError):
                continue
        else:
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    out: list[Expert] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            limit = int(item.get("max_chars") or MAX_OUTPUT_CHARS)
        except (TypeError, ValueError):
            limit = MAX_OUTPUT_CHARS
        out.append(Expert(
            name=name[:20],
            description=str(item.get("description") or "").strip()[:120],
            system=str(item.get("system") or "").strip(),
            provider_id=str(item.get("provider_id") or "").strip(),
            max_chars=max(200, min(6000, limit)),
            material_limit=MAX_MATERIAL_CHARS,
            enabled=bool(item.get("enabled", True)),
        ))
    return out


def find_expert(experts: list[Expert], name: str) -> Expert | None:
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for expert in experts:
        if expert.name.lower() == wanted:
            return expert
    # 允许前缀匹配（「审稿」能命中「审稿人」）
    for expert in experts:
        if expert.name.lower().startswith(wanted) or wanted.startswith(expert.name.lower()):
            return expert
    return None


# ---------------------------------------------------------------- 调用
def _text_of(resp: Any) -> str:
    text = str(getattr(resp, "completion_text", "") or "").strip()
    if text:
        return text
    try:
        result = getattr(resp, "result", None)
        if result is not None:
            return str(result).strip()
    except Exception:
        pass
    return ""


async def delegate(
    provider: Any,
    *,
    task: str,
    material: str = "",
    expert: Expert | None = None,
    max_output_chars: int | None = None,
    max_material_chars: int | None = None,
) -> DelegateResult:
    """让模型在独立上下文里完成一件小事，返回结果文本。"""
    who = expert or DEFAULT_EXPERT
    goal = str(task or "").strip()
    if not goal:
        raise DelegateError("没有给任务")
    if len(goal) > 800:
        raise DelegateError("任务描述太长（>800 字），拆小一点")
    if provider is None:
        raise DelegateError("当前没有可用的模型 provider")

    limit = int(max_output_chars or who.max_chars or MAX_OUTPUT_CHARS)
    mat_limit = int(max_material_chars or who.material_limit or MAX_MATERIAL_CHARS)
    body = str(material or "").strip()
    if len(body) > mat_limit:
        body = body[:mat_limit].rstrip() + "\n…（材料过长，已截断）"

    prompt = PROMPT_TEMPLATE.format(task=goal, material=body or "（无）", limit=limit)
    system = who.system or SYSTEM_PROMPT
    kwargs: dict[str, Any] = {"prompt": prompt}
    try:
        signature = inspect.signature(getattr(provider, "text_chat"))
        if "system_prompt" in signature.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
        ):
            kwargs["system_prompt"] = system
    except Exception:
        kwargs["system_prompt"] = system

    try:
        resp = await provider.text_chat(**kwargs)
    except TypeError:
        try:
            resp = await provider.text_chat(prompt=prompt)
        except Exception as exc:
            raise DelegateError(f"子代理调用失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    except Exception as exc:
        raise DelegateError(f"子代理调用失败：{type(exc).__name__}: {str(exc)[:120]}") from exc

    text = _text_of(resp)
    if not text:
        raise DelegateError("子代理没有产出内容（可能是空回复或调用被拒）")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…（已截断）"

    model = ""
    try:
        model = str(getattr(getattr(provider, "meta", None), "model", "") or "")
    except Exception:
        model = ""

    return DelegateResult(
        text=text,
        prompt_chars=len(prompt),
        output_chars=len(text),
        model=model,
        expert=who.name if who is not DEFAULT_EXPERT else "",
    )
