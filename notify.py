"""「认知 → 人格 → 记忆」的转接通道。

为什么不直接写记忆库：
- 记忆写入的措辞、去重、时序、会话归属都应该由**人格**统一决定；
- 认知保持"手"的分工，不需要知道记忆长什么样；
- 万一记忆插件没装/没启用，也只是少记一笔，不影响工具本身。

通道：通过 `context.get_all_stars()` 找到人格插件实例（`StarMetadata.star_cls`），
调用它的 `note_external_event()`；找不到就安静降级（返回原因，不抛异常）。
"""

from __future__ import annotations

import asyncio
from typing import Any

_PERSONA_HINT = "astrbot_plugin_storyteller"


def find_persona(context: Any) -> Any:
    """在已加载插件里找人格实例。找不到返回 None（未装/未激活都是正常情况）。"""
    try:
        stars = context.get_all_stars() or []
    except Exception:
        return None
    for meta in stars:
        module = str(getattr(meta, "module_path", "") or "")
        name = str(getattr(meta, "name", "") or "")
        module_name = str(getattr(meta, "module", "") or "")
        if _PERSONA_HINT in (module + name + module_name):
            instance = getattr(meta, "star_cls", None)
            if instance is not None:
                return instance
    return None


def find_storyteller(context: Any) -> Any:
    """在已加载插件里找陪伴插件实例（为了拿它的 EmojiStore）。找不到返回 None。"""
    try:
        stars = context.get_all_stars() or []
    except Exception:
        return None
    for meta in stars:
        module = str(getattr(meta, "module_path", "") or "")
        name = str(getattr(meta, "name", "") or "")
        module_name = str(getattr(meta, "module", "") or "")
        if "storyteller" in (module + name + module_name):
            instance = getattr(meta, "star_cls", None)
            if instance is not None:
                return instance
    return None


async def notify_persona(
    context: Any,
    event: Any,
    *,
    kind: str,
    text: str,
    meta: dict | None = None,
    timeout: float = 6.0,
) -> dict:
    """把"我做了什么"转告人格。返回 {ok, reason, memory_id?}，绝不抛异常。"""
    instance = find_persona(context)
    if instance is None:
        return {"ok": False, "reason": "persona_not_loaded"}
    handler = getattr(instance, "note_external_event", None)
    if not callable(handler):
        return {"ok": False, "reason": "persona_no_entry"}
    try:
        result = await asyncio.wait_for(
            handler(kind=kind, text=text, event=event, meta=meta or {}),
            timeout=timeout,
        )
        return dict(result or {"ok": False, "reason": "empty_result"})
    except asyncio.TimeoutError:
        return {"ok": False, "reason": "timeout"}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
