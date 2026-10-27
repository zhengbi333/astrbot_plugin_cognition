"""平台社交动作：戳一戳、点赞。

为什么不塞进通用工具里：这类动作是**平台特有**的，失败原因和文件工具完全不同
（平台不支持、权限不足、频率限制），需要单独兜底与单独审计。

安全模型（比"随便填个 QQ 号"严格得多）：
- 默认目标 = **当前说话的人**（戳回去/给他点个赞），这是最不容易被滥用的语义；
- 允许显式指定目标，但**只接受当前会话里说话的那个人或机器人自己**；
- 想戳别人？不支持。这样即使有人诱导，也无法把动作打向无关的 QQ 号。

实现上走 AstrBot 事件对象上的 `bot.call_action`（aiocqhttp 的 CQHttp），
所以不依赖框架是否包了对应的高层方法；非 aiocqhttp 平台会明确报"这个平台不支持"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class QqActionError(Exception):
    """平台动作失败（信息面向模型可读）。"""


@dataclass(slots=True)
class ActionTarget:
    group_id: str
    user_id: str
    is_group: bool


def _attr(event: Any, name: str, default: str = "") -> str:
    try:
        value = getattr(event, name, None)
        if callable(value):
            value = value()
        return str(value or default)
    except Exception:
        return default


def resolve_target(event: Any) -> ActionTarget:
    """从事件里取出"这场对话发生在哪、谁在说话"。"""
    group_id = _attr(event, "get_group_id")
    user_id = _attr(event, "get_sender_id")
    if not user_id:
        raise QqActionError("拿不到说话人，没法决定对谁做这个动作")
    return ActionTarget(group_id=group_id, user_id=user_id, is_group=bool(group_id))


def _bot_of(event: Any) -> Any:
    bot = getattr(event, "bot", None)
    if bot is None or not hasattr(bot, "call_action"):
        raise QqActionError("当前平台不支持这个动作（只有 aiocqhttp 能戳一戳/点赞）")
    return bot


def _check_target(event: Any, target_id: str, *, self_id: str) -> str:
    """把目标限制在「说话人本人」或「机器人自己」。"""
    wanted = str(target_id or "").strip()
    current = resolve_target(event)
    if not wanted:
        return current.user_id
    if wanted == current.user_id:
        return wanted
    if wanted and wanted == self_id:
        return wanted
    raise QqActionError("只能对当前说话的人（或我自己）做这个动作，不能打到别人身上")


async def poke(event: Any, target_id: str = "", *, self_id: str = "") -> str:
    """戳一戳：群聊走 group_poke，私聊走 send_poke。返回一句可读的结果。"""
    bot = _bot_of(event)
    current = resolve_target(event)
    target = _check_target(event, target_id, self_id=self_id or current.user_id)
    try:
        if current.is_group:
            await bot.call_action("group_poke", group_id=int(current.group_id), user_id=int(target))
        else:
            await bot.call_action("send_poke", user_id=int(target))
    except QqActionError:
        raise
    except Exception as exc:
        raise QqActionError(f"戳一戳失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    if target == current.user_id:
        return "戳了一下对方"
    return "戳了一下自己"


async def like(event: Any, target_id: str = "", *, times: int = 1, cap: int = 10, self_id: str = "") -> str:
    """给名片点赞（标准 OneBot send_like）。times 会被收敛到 1..cap。"""
    bot = _bot_of(event)
    current = resolve_target(event)
    target = _check_target(event, target_id, self_id=self_id or current.user_id)
    count = max(1, min(max(1, int(cap)), int(times or 1)))
    try:
        await bot.call_action("send_like", user_id=int(target), times=count)
    except QqActionError:
        raise
    except Exception as exc:
        raise QqActionError(f"点赞失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    return f"点了 {count} 个赞"


def describe_event_kind(event: Any) -> str:
    """判断这条事件是不是"被戳了一下"（供人格层决定要不要回应）。

    NapCat/SnowLuma 的戳一戳是 notice 事件，AstrBot 的 on_notice 会把它转成消息事件；
    不同实现给出的字段不一样，所以做宽松匹配：raw 里有 poke / 戳，或文本里带戳一戳标记。
    """
    raw = getattr(event, "raw_message", None) or {}
    try:
        if isinstance(raw, dict):
            notice_type = str(raw.get("notice_type") or "")
            sub_type = str(raw.get("sub_type") or "")
            if "poke" in notice_type.lower() or "poke" in sub_type.lower():
                return "poke"
    except Exception:
        pass
    text = _attr(event, "message_str")
    if "戳一戳" in text or "拍一拍" in text or "[poke]" in text.lower():
        return "poke"
    return ""
