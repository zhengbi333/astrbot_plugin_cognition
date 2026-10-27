"""子代理（对应 DSH 的 subagent）：把一件独立的小任务交给"另一个自己"跑。

和"并行进程"的区别：并行跑的是**脚本**，没有推理能力；子代理跑的是**模型**，
能"想一下再给结果"。实现上就是用当前 provider 开一次**不带当前对话上下文**的独立请求，
只把任务与材料交给它 —— 好处是主对话不被污染，坏处是要额外付一次模型调用。

限制（必须有）：
- 单次材料长度上限（默认 4000 字），避免把整段对话塞进去；
- 输出上限（默认 2000 字）；
- 一次只跑一个（不做并发子代理，避免成本失控）；
- 失败如实返回原因，不假装给了结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_MATERIAL_CHARS = 4000
MAX_OUTPUT_CHARS = 2000


class DelegateError(Exception):
    """子代理失败（信息面向模型可读）。"""


@dataclass(slots=True)
class DelegateResult:
    text: str
    prompt_chars: int
    output_chars: int
    model: str = ""


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
    max_output_chars: int = MAX_OUTPUT_CHARS,
    max_material_chars: int = MAX_MATERIAL_CHARS,
) -> DelegateResult:
    """让模型在独立上下文里完成一件小事，返回结果文本。"""
    goal = str(task or "").strip()
    if not goal:
        raise DelegateError("没有给任务")
    if len(goal) > 800:
        raise DelegateError("任务描述太长（>800 字），拆小一点")
    body = str(material or "").strip()
    if len(body) > max_material_chars:
        body = body[:max_material_chars].rstrip() + "\n…（材料过长，已截断）"
    if provider is None:
        raise DelegateError("当前没有可用的模型 provider")

    prompt = PROMPT_TEMPLATE.format(task=goal, material=body or "（无）", limit=max_output_chars)
    kwargs: dict[str, Any] = {"prompt": prompt}
    try:
        import inspect

        signature = inspect.signature(getattr(provider, "text_chat"))
        if "system_prompt" in signature.parameters:
            kwargs["system_prompt"] = SYSTEM_PROMPT
        elif any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            kwargs["system_prompt"] = SYSTEM_PROMPT
    except Exception:
        kwargs["system_prompt"] = SYSTEM_PROMPT

    try:
        resp = await provider.text_chat(**kwargs)
    except TypeError:
        # 有些 provider 不收 system_prompt，退一步只发 prompt
        try:
            resp = await provider.text_chat(prompt=prompt)
        except Exception as exc:
            raise DelegateError(f"子代理调用失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    except Exception as exc:
        raise DelegateError(f"子代理调用失败：{type(exc).__name__}: {str(exc)[:120]}") from exc

    text = _text_of(resp)
    if not text:
        raise DelegateError("子代理没有产出内容（可能是空回复或调用被拒）")
    if len(text) > max_output_chars:
        text = text[:max_output_chars].rstrip() + "…（已截断）"

    model = ""
    try:
        meta = getattr(provider, "meta", None)
        model = str(getattr(meta, "model", "") or "")
    except Exception:
        model = ""

    return DelegateResult(
        text=text,
        prompt_chars=len(prompt),
        output_chars=len(text),
        model=model,
    )
