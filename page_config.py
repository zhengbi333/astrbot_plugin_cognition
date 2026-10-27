"""设置面板的后端：读配置 schema + 写回配置。

设计原则：
1. **每一项都带说明**（description + hint），面板上直接显示"这是干什么的"，
   用户不需要去翻文档或对着 `_conf_schema.json` 猜。
2. **写回要安全**：原子写（先 .tmp 再 replace）、写前备份、写后立刻让插件读到新值。
3. **只写 `_conf_schema.json` 里声明过的键**，避免前端塞任意字段进配置。
4. 敏感项（密码/token 之类）不在这里出现，配置里也没有。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from . import PLUGIN_NAME

SCHEMA_FILE = "_conf_schema.json"

# 面板上按这个顺序分组展示（没列出的组会自动追加到末尾）
GROUP_ORDER = (
    "general", "auth", "workspace", "fs", "web", "download", "send",
    "office", "image", "archive", "exec", "shell", "tasks", "parallel",
    "goals", "delegate", "audit",
)

GROUP_TITLES: dict[str, str] = {
    "general": "基础",
    "auth": "权限（谁能用）",
    "workspace": "工作区（在哪干活）",
    "fs": "文件读写",
    "web": "网页与搜索",
    "download": "下载",
    "send": "发文件",
    "office": "文档（docx/xlsx/pptx）",
    "image": "图片处理",
    "archive": "压缩包",
    "exec": "脚本执行（仅管理员）",
    "shell": "命令执行（默认关）",
    "tasks": "后台任务",
    "parallel": "并行执行",
    "goals": "长期目标",
    "delegate": "子代理（专家）",
    "audit": "动作记录",
}

# 控件类型 → 前端怎么渲染
WIDGETS = {
    "bool": "switch",
    "int": "number",
    "float": "number",
    "string": "text",
    "text": "textarea",
    "list": "textarea",
    "file": "text",
    "object": "group",
    "template_list": "textarea",
    "dict": "textarea",
}


def _schema_path(plugin: Any) -> Path:
    """_conf_schema.json 就在本模块所在目录（插件目录），不用推导。"""
    return Path(__file__).parent / SCHEMA_FILE


def load_schema(plugin: Any) -> dict[str, Any]:
    """读插件自带的 _conf_schema.json。"""
    path = _schema_path(plugin)
    if not path.is_file():
        raise FileNotFoundError("找不到 schema：{}".format(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _config_file(plugin: Any) -> Path:
    """AstrBot 的插件配置文件位置。"""
    candidates = [
        Path("C:/Users/阡然/.astrbot/data/config") / f"{PLUGIN_NAME}_config.json",
    ]
    for path in candidates:
        if path.parent.is_dir():
            return path
    # 兜底：从数据目录往上找 config
    data = Path(getattr(plugin, "data_dir"))
    guess = data.parent.parent / "config" / f"{PLUGIN_NAME}_config.json"
    return guess


def _flatten(node: dict[str, Any], *, trail: str = "") -> list[dict[str, Any]]:
    """把 schema 摊平成「一行一个配置项」，每项自带说明。"""
    rows: list[dict[str, Any]] = []
    items = node.get("items")
    if not isinstance(items, dict):
        return rows
    for key, spec in items.items():
        if not isinstance(spec, dict):
            continue
        dotted = f"{trail}.{key}" if trail else key
        kind = str(spec.get("type") or "string").lower()
        if kind == "object" and isinstance(spec.get("items"), dict):
            rows.extend(_flatten(spec, trail=dotted))
            continue
        rows.append({
            "key": dotted,
            "label": str(spec.get("description") or key),
            "hint": str(spec.get("hint") or ""),
            "type": kind,
            "widget": WIDGETS.get(kind, "text"),
            "default": spec.get("default"),
            "options": spec.get("options") or [],
        })
    return rows


def describe(plugin: Any) -> dict[str, Any]:
    """给面板的完整配置描述：分组 + 每项的说明 + 当前值。"""
    schema = load_schema(plugin)
    groups: list[dict[str, Any]] = []
    for name, section in schema.items():
        if not isinstance(section, dict):
            continue
        # 关键：必须带分组前缀（general.enabled），否则前端提交短名
        # 会过不了后端的 _allowed_keys 校验（真机报「这些配置项不存在：enabled」）。
        rows = _flatten(section, trail=name)
        if not rows:
            continue
        # 当前值
        for row in rows:
            try:
                value = plugin.config.get(row["key"], row["default"])
            except Exception:
                value = row["default"]
            row["value"] = value
        groups.append({
            "key": name,
            "title": GROUP_TITLES.get(name, str(section.get("description") or name)),
            "note": str(section.get("description") or ""),
            "fields": rows,
        })
    order = {key: index for index, key in enumerate(GROUP_ORDER)}
    groups.sort(key=lambda g: order.get(g["key"], 999))
    return {
        "ok": True,
        "groups": groups,
        "config_file": str(_config_file(plugin)),
        "schema_file": str(_schema_path(plugin)),
        "count": sum(len(g["fields"]) for g in groups),
    }


def _allowed_keys(schema: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for name, section in schema.items():
        if not isinstance(section, dict):
            continue
        for row in _flatten(section, trail=name):
            allowed.add(row["key"])
    return allowed


def _coerce(kind: str, value: Any, default: Any) -> Any:
    """按 schema 的类型把前端传来的值转回来。"""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default or 0)
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default or 0.0)
    if kind in ("list", "object", "dict", "template_list"):
        if isinstance(value, (list, dict)):
            return value
        text = str(value or "").strip()
        if not text:
            return [] if kind == "list" else {}
        for candidate in (text, text.replace("\\", "/")):
            try:
                return json.loads(candidate)
            except (ValueError, TypeError):
                continue
        return default
    return str(value if value is not None else (default or ""))


def update(plugin: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """写回配置：只认 schema 声明过的键，原子写 + 备份。"""
    schema = load_schema(plugin)
    allowed = _allowed_keys(schema)
    incoming = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        raise ValueError("请求体要带 values 对象")
    unknown = [key for key in incoming if key not in allowed]
    if unknown:
        raise ValueError(f"这些配置项不存在：{'、'.join(unknown[:5])}")

    path = _config_file(plugin)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = {}

    # 备份（同目录、带时间戳，不覆盖）
    if path.is_file():
        backup = path.with_name(f"{path.stem}.bak-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            backup = None
    else:
        backup = None

    changed: list[str] = []
    for key, value in incoming.items():
        # 找这一项的类型
        kind = "string"
        default = None
        for name, section in schema.items():
            if not isinstance(section, dict):
                continue
            for row in _flatten(section, trail=name):
                if row["key"] == key:
                    kind = row["type"]
                    default = row["default"]
                    break
        new_value = _coerce(kind, value, default)
        parts = key.split(".")
        node = raw
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        if node.get(parts[-1]) != new_value:
            node[parts[-1]] = new_value
            changed.append(key)

    if changed:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        # 让插件立刻读到新值（AstrBot 的配置对象支持 update）
        try:
            plugin.config.update(raw) if hasattr(plugin.config, "update") else None
        except Exception:
            pass

    logger.info("[Toolbox] 面板写配置：%s 项变更", len(changed))
    return {
        "ok": True,
        "changed": changed,
        "backup": str(backup) if backup else "",
        "reload_hint": "部分配置需要重载插件才完全生效（面板里有重载说明）" if changed else "",
    }
