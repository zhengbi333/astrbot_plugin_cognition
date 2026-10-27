"""账号分级与动作授权。

为什么单独成模块：之前只有"是不是 owner"一个布尔，导致所有工具要么全开、要么全关。
现在分三级，让"谁能做什么"可以按配置精细控制：

- **admin（管理员）**：白名单里的 QQ 号。可用代码执行、后台任务、全盘访问；
- **member（成员）**：可用工作区内的读写、搜索、文档、发送；
- **guest（访客）**：默认只能聊天 + 查资料（搜索/抓网页），不能写、不能执行、不能发送。

设计原则：
1. **默认 fail-closed**：认不出是谁 → 按 guest 处理；
2. 名单为空 → **没有 admin**（不是"所有人都是 admin"）；
3. 兼容旧配置：`auth.admins` 为空时回退读 `exec.owner_ids`，老用户不用改配置。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LEVEL_ADMIN = "admin"
LEVEL_MEMBER = "member"
LEVEL_GUEST = "guest"

_LEVEL_ORDER = {LEVEL_GUEST: 0, LEVEL_MEMBER: 1, LEVEL_ADMIN: 2}

# 动作 → 最低所需等级。没列出的动作默认按 member。
_ACTION_MIN_LEVEL: dict[str, str] = {
    # 只读类：访客也能用
    "read": LEVEL_GUEST,
    "search": LEVEL_GUEST,
    "web": LEVEL_GUEST,
    "chat": LEVEL_GUEST,
    # 工作区内写入/产出：成员起
    "write": LEVEL_MEMBER,
    "office": LEVEL_MEMBER,
    "download": LEVEL_MEMBER,
    "send": LEVEL_MEMBER,
    "archive": LEVEL_MEMBER,
    "image": LEVEL_MEMBER,
    # 执行类：只有管理员
    "exec": LEVEL_ADMIN,
    "task": LEVEL_ADMIN,
    "shell": LEVEL_ADMIN,
    "full_access": LEVEL_ADMIN,
}


def parse_ids(value: str | list | tuple | set | None) -> set[str]:
    """解析账号名单（逗号/分号/空白分隔）。"""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = [str(x) for x in value]
    else:
        items = str(value).replace("，", ",").replace(";", ",").replace(" ", ",").split(",")
    return {item.strip() for item in items if item.strip()}


@dataclass(slots=True)
class Principal:
    """当前说话人的身份。"""

    user_id: str
    level: str = LEVEL_GUEST

    @property
    def is_admin(self) -> bool:
        return self.level == LEVEL_ADMIN

    @property
    def is_member(self) -> bool:
        return _LEVEL_ORDER.get(self.level, 0) >= _LEVEL_ORDER[LEVEL_MEMBER]

    def at_least(self, level: str) -> bool:
        return _LEVEL_ORDER.get(self.level, 0) >= _LEVEL_ORDER.get(level, 0)


def _cfg(plugin: Any, dotted: str, default: Any = None) -> Any:
    try:
        return plugin.config.get(dotted, default)
    except Exception:
        return default


def resolve_principal(plugin: Any, event: Any) -> Principal:
    """判断当前说话人属于哪一级。认不出 → guest。"""
    sender = ""
    try:
        sender = str(event.get_sender_id() or "").strip()
    except Exception:
        sender = ""
    if not sender:
        return Principal(user_id="", level=LEVEL_GUEST)

    admins = parse_ids(_cfg(plugin, "auth.admins", ""))
    if not admins:
        # 兼容旧配置：没有 auth.admins 时沿用 exec.owner_ids
        admins = parse_ids(_cfg(plugin, "exec.owner_ids", ""))
    if sender in admins:
        return Principal(user_id=sender, level=LEVEL_ADMIN)

    members = parse_ids(_cfg(plugin, "auth.members", ""))
    if sender in members:
        return Principal(user_id=sender, level=LEVEL_MEMBER)

    # 未登记的人：按配置决定是 member 还是 guest（默认 guest）
    try:
        open_to_all = bool(plugin.config.bool("auth.members_open", False))
    except Exception:
        open_to_all = False
    return Principal(user_id=sender, level=LEVEL_MEMBER if open_to_all else LEVEL_GUEST)


def required_level(action: str) -> str:
    return _ACTION_MIN_LEVEL.get(str(action or "").strip().lower(), LEVEL_MEMBER)


def can(principal: Principal, action: str) -> bool:
    """当前身份能否执行该动作。"""
    return principal.at_least(required_level(action))


def deny_reason(principal: Principal, action: str) -> str:
    """给模型的拒绝文案（说清楚缺什么权限，便于使用者去配置）。"""
    need = required_level(action)
    label = {LEVEL_ADMIN: "管理员", LEVEL_MEMBER: "成员", LEVEL_GUEST: "访客"}.get(need, need)
    return f"这个操作需要「{label}」权限，你现在的等级不够（可以到插件配置的 auth 里加白名单）。"
