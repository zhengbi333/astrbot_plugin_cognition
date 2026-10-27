"""按会话解析工作区，以及"能访问到多广"的访问级别。

三种工作区模式：
- `shared`：所有会话共用一个工作区（默认，和以前一样）；
- `per_session`：每个会话在 `workspace/sessions/<哈希>/` 下有自己的一块，
  互不可见 —— 适合"每个群各干各的活"；
- `mapped`：在配置里显式指定"某个会话 → 某个目录"（可以是任意绝对路径），
  用于"这个群就用 D:\\bot\\群A"这种分配。

工作区只放**运行产物**（脚本、生成的文档、下载的文件、日志）。
会话数据（记忆/画像/时间线）归记忆插件，不要混进来。

访问级别：
- `workspace`：路径必须落在本会话的工作区内（默认，最安全）；
- `full`：允许工作区之外，但**仍然**受 `workspace.full_roots` 白名单根目录约束，
  并且**只有 admin 能用**（见 policy.py）。这就是"和 DSH 差不多"的全盘访问，
  只不过外面还套了一圈白名单。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODES = ("shared", "per_session", "mapped")
ACCESS_WORKSPACE = "workspace"
ACCESS_FULL = "full"


@dataclass(slots=True)
class WorkspacePlan:
    root: Path
    mode: str
    access: str
    extra_roots: list[Path]

    @property
    def is_full(self) -> bool:
        return self.access == ACCESS_FULL


def session_key(event: Any) -> str:
    """会话标识：优先用统一消息来源，拿不到就用平台+发送者拼一个。"""
    for attr in ("unified_msg_origin",):
        try:
            value = str(getattr(event, attr, "") or "").strip()
            if value:
                return value
        except Exception:
            pass
    try:
        platform = str(getattr(event, "get_platform_name", lambda: "")() or "")
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group = str(getattr(event, "get_group_id", lambda: "")() or "")
    except Exception:
        platform = sender = group = ""
    return f"{platform}:{group or sender or 'unknown'}"


def _safe_dir_name(key: str) -> str:
    """把会话键变成安全的目录名：可读前缀 + 短哈希，避免中文/符号问题。"""
    digest = hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:12]
    prefix = re.sub(r"[^0-9A-Za-z]+", "", key.split(":")[-1] or "")[:16]
    return f"{prefix or 's'}-{digest}"


def _cfg(plugin: Any, dotted: str, default: Any = None) -> Any:
    try:
        return plugin.config.get(dotted, default)
    except Exception:
        return default


def _as_mapping(value: Any) -> dict[str, str]:
    """把配置里的映射读成 dict（容忍 JSON 字符串 / dict）。"""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if str(v or "").strip()}
    if isinstance(value, str) and value.strip():
        import json

        text = str(value).strip()
        data = None
        for candidate in (text, text.replace("\\", "/")):
            try:
                data = json.loads(candidate)
                break
            except (ValueError, TypeError):
                continue
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(v or "").strip()}
    return {}


def resolve_plan(plugin: Any, event: Any, *, principal: Any = None) -> WorkspacePlan:
    """按配置与会话算出这次该用哪个工作区、能访问到多广。"""
    mode = str(_cfg(plugin, "workspace.mode", "shared") or "shared").strip().lower()
    if mode not in MODES:
        mode = "shared"

    base = Path(getattr(plugin, "workspace_root"))
    key = session_key(event)

    if mode == "mapped":
        mapping = _as_mapping(_cfg(plugin, "workspace.mapped", {}))
        target = mapping.get(key) or mapping.get("*") or ""
        root = Path(target).expanduser() if target else base
    elif mode == "per_session":
        root = base / "sessions" / _safe_dir_name(key)
    else:
        root = base

    # 访问级别：配置要 full，且调用者确实是 admin，才给 full
    access = str(_cfg(plugin, "workspace.access", ACCESS_WORKSPACE) or ACCESS_WORKSPACE).strip().lower()
    if access not in (ACCESS_WORKSPACE, ACCESS_FULL):
        access = ACCESS_WORKSPACE
    if access == ACCESS_FULL and principal is not None and not getattr(principal, "is_admin", False):
        access = ACCESS_WORKSPACE

    extra: list[Path] = []
    if access == ACCESS_FULL:
        raw = _cfg(plugin, "workspace.full_roots", "")
        for item in _as_raw_list(raw):
            try:
                path = Path(item).expanduser()
            except Exception:
                continue
            if str(path).strip():
                extra.append(path)

    return WorkspacePlan(root=root, mode=mode, access=access, extra_roots=extra)


def _as_raw_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).replace("，", ",").replace(";", ",").replace("\n", ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def describe(plan: WorkspacePlan) -> str:
    """给模型/使用者的可读说明。"""
    parts = [f"模式 {plan.mode}", f"访问 {plan.access}", f"目录 {plan.root}"]
    if plan.is_full:
        roots = "、".join(str(p) for p in plan.extra_roots) or "（未限制，全盘）"
        parts.append(f"可访问根：{roots}")
    return "；".join(parts)
