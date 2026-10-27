"""Web API：供面板调用的后端接口。

路由前缀 /astrbot_plugin_cognition，通过 context.register_web_api 注册；
前端用 AstrBotPluginPage bridge（apiGet / apiPost）调用。

请求/响应对象三级回退（与人格插件同一套写法）：
1. AstrBot 面板运行时的 quart 风格代理；
2. astrbot.api.web 的请求代理；
3. quart 自带 request。
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from astrbot.api import logger

from . import PLUGIN_DISPLAY, PLUGIN_NAME, PLUGIN_VERSION
from . import delegate as delegate_mod
from . import goals as goals_mod
from . import skills as skills_mod

try:  # pragma: no cover
    from astrbot.api.web import error_response, json_response

    _HAS_WEB_RESPONSE = True
except Exception:  # pragma: no cover
    _HAS_WEB_RESPONSE = False
    from quart import jsonify  # type: ignore

try:  # pragma: no cover
    from astrbot.dashboard.asgi_runtime import request as _request

    _REQUEST_SOURCE = "asgi"
except Exception:  # pragma: no cover
    try:  # pragma: no cover
        from astrbot.api.web import request as _request

        _REQUEST_SOURCE = "web"
    except Exception:  # pragma: no cover
        from quart import request as _request  # type: ignore

        _REQUEST_SOURCE = "quart"


async def _ok(data: Any = None) -> Any:
    if _HAS_WEB_RESPONSE:
        return json_response(data)
    return jsonify({} if data is None else data)


async def _fail(message: str, status_code: int = 400) -> Any:
    if _HAS_WEB_RESPONSE:
        return error_response(message, status_code=status_code)
    return jsonify({"status": "error", "message": message}), status_code


async def _json_body(default: Any = None) -> Any:
    """读取 JSON 请求体，兼容三种请求来源。"""
    try:
        if _REQUEST_SOURCE == "asgi":
            value = getattr(_request, "json", None)
            if inspect.isawaitable(value):
                return await value
            if callable(value):
                result = value()
                return await result if inspect.isawaitable(result) else result
            if value is not None:
                return value
        else:
            method = getattr(_request, "get_json", None)
            if callable(method):
                result = method(silent=True)
                return await result if inspect.isawaitable(result) else result
    except Exception:
        pass
    for attr in ("body", "data"):
        try:
            raw = getattr(_request, attr, None)
            if inspect.isawaitable(raw):
                raw = await raw
            if isinstance(raw, (bytes, bytearray)) and raw:
                return json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(raw, str) and raw.strip():
                return json.loads(raw)
        except Exception:
            continue
    return default


class CognitionWebApi:
    """面板后端：状态、专家、目标、技能、动作记录。"""

    def __init__(self, plugin: Any):
        self._plugin = plugin

    # ------------------------------------------------------------ 注册
    def mount(self, context: Any) -> None:
        if not hasattr(context, "register_web_api"):
            logger.warning("[Toolbox] 当前 AstrBot 不支持 register_web_api，面板接口未注册")
            return
        register = context.register_web_api
        prefix = f"/{PLUGIN_NAME}"
        routes = [
            (f"{prefix}/overview", self.overview, ["GET"], "Cognition overview"),
            (f"{prefix}/experts", self.experts, ["GET"], "Cognition experts list"),
            (f"{prefix}/goals", self.goals_list, ["GET"], "Cognition goals list"),
            (f"{prefix}/goals/add", self.goals_add, ["POST"], "Cognition goal add"),
            (f"{prefix}/goals/close", self.goals_close, ["POST"], "Cognition goal close"),
            (f"{prefix}/skills", self.skills_list, ["GET"], "Cognition skills list"),
            (f"{prefix}/skills/read", self.skills_read, ["GET"], "Cognition skill detail"),
            (f"{prefix}/audit", self.audit_log, ["GET"], "Cognition recent actions"),
            (f"{prefix}/audit/clear", self.audit_clear, ["POST"], "Cognition audit clear"),
        ]
        for path, handler, methods, desc in routes:
            try:
                register(path, handler, methods, desc)
            except Exception as exc:  # pragma: no cover
                logger.warning("[Toolbox] 注册面板路由失败 %s: %s", path, exc)

    # ------------------------------------------------------------ 总览
    async def overview(self) -> Any:
        plugin = self._plugin
        payload: dict[str, Any] = {
            "ok": True,
            "display": PLUGIN_DISPLAY,
            "version": PLUGIN_VERSION,
            "data_dir": str(plugin.data_dir),
        }
        try:
            payload["workspace"] = {
                "root": str(plugin.workspace_root),
                "mode": str(plugin.config.get("workspace.mode", "shared") or "shared"),
                "access": str(plugin.config.get("workspace.access", "workspace") or "workspace"),
            }
        except Exception as exc:
            payload["workspace"] = {"error": str(exc)}

        try:
            payload["auth"] = {
                "admins": str(plugin.config.get("auth.admins", "") or ""),
                "members": str(plugin.config.get("auth.members", "") or ""),
                "members_open": bool(plugin.config.bool("auth.members_open", False)),
                "fallback_owner": str(plugin.config.get("exec.owner_ids", "") or ""),
            }
        except Exception as exc:
            payload["auth"] = {"error": str(exc)}

        try:
            payload["delegate"] = {
                "used": plugin.delegate_budget.used(),
                "limit": plugin.delegate_budget.daily_limit,
                "left": plugin.delegate_budget.left(),
                "experts": len(plugin._experts()),
            }
        except Exception as exc:
            payload["delegate"] = {"error": str(exc)}

        try:
            payload["tools"] = self._tool_names(plugin)
        except Exception as exc:
            payload["tools"] = {"error": str(exc)}

        try:
            payload["counts"] = {
                "goals_open": len(plugin.goals.list()),
                "skills": len(skills_mod.scan(plugin.skills_root)),
                "audit": len(plugin._audit.load()),
            }
        except Exception as exc:
            payload["counts"] = {"error": str(exc)}

        return await _ok(payload)

    @staticmethod
    def _tool_names(plugin: Any) -> dict[str, Any]:
        """从已注册的功能表里数工具（拿不到就返回空）。"""
        names: list[str] = []
        try:
            manager = plugin.context.get_llm_tool_manager()
            for tool in getattr(manager, "func_list", []) or []:
                name = str(getattr(tool, "name", "") or "")
                if name.startswith("toolbox_"):
                    names.append(name.replace("toolbox_", ""))
        except Exception:
            pass
        return {"total": len(names), "names": sorted(names)}

    # ------------------------------------------------------------ 专家
    async def experts(self) -> Any:
        plugin = self._plugin
        items: list[dict[str, Any]] = []
        try:
            for expert in plugin._experts():
                items.append({
                    "name": expert.name,
                    "description": expert.description,
                    "system": expert.system[:400],
                    "provider_id": expert.provider_id,
                    "max_chars": expert.max_chars,
                    "enabled": expert.enabled,
                })
        except Exception as exc:
            return await _fail(f"读取专家失败：{exc}")
        budget: dict[str, Any] = {}
        try:
            budget = {
                "used": plugin.delegate_budget.used(),
                "limit": plugin.delegate_budget.daily_limit,
                "left": plugin.delegate_budget.left(),
            }
        except Exception:
            pass
        return await _ok({"experts": items, "budget": budget})

    # ------------------------------------------------------------ 目标
    async def goals_list(self) -> Any:
        plugin = self._plugin
        try:
            include_closed = False
            try:
                raw = str(getattr(_request, "args", {}) or {})
                include_closed = "all" in raw
            except Exception:
                include_closed = False
            items = [
                {
                    "id": goal.id,
                    "title": goal.title,
                    "note": goal.note,
                    "state": goal.state,
                    "created": goal.created,
                    "updated": goal.updated,
                    "line": goal.line(),
                }
                for goal in plugin.goals.list(include_closed=include_closed, limit=100)
            ]
            return await _ok({"goals": items})
        except Exception as exc:
            return await _fail(f"读取目标失败：{exc}")

    async def goals_add(self) -> Any:
        payload = await _json_body(default={}) or {}
        title = str(payload.get("title") or "").strip()
        note = str(payload.get("note") or "").strip()
        try:
            goal = self._plugin.goals.add(title, note)
            return await _ok({"goal": {"id": goal.id, "title": goal.title}})
        except goals_mod.GoalError as exc:
            return await _fail(str(exc))
        except Exception as exc:
            return await _fail(f"新建目标失败：{exc}")

    async def goals_close(self) -> Any:
        payload = await _json_body(default={}) or {}
        goal_id = str(payload.get("goal_id") or "").strip()
        state = str(payload.get("state") or "done").strip().lower()
        note = str(payload.get("note") or "").strip()
        try:
            goal = self._plugin.goals.close(goal_id, state=state, note=note)
            return await _ok({"goal": {"id": goal.id, "state": goal.state}})
        except goals_mod.GoalError as exc:
            return await _fail(str(exc))
        except Exception as exc:
            return await _fail(f"更新目标失败：{exc}")

    # ------------------------------------------------------------ 技能
    async def skills_list(self) -> Any:
        try:
            items = [
                {"name": s.name, "description": s.description, "chars": s.chars, "file": s.path.name}
                for s in skills_mod.scan(self._plugin.skills_root)
            ]
            return await _ok({"skills": items})
        except Exception as exc:
            return await _fail(f"读取技能失败：{exc}")

    async def skills_read(self) -> Any:
        name = ""
        try:
            args = getattr(_request, "args", {}) or {}
            name = str(args.get("name") or "").strip()
        except Exception:
            name = ""
        if not name:
            return await _fail("缺少 name 参数")
        try:
            skill = skills_mod.find(self._plugin.skills_root, name)
            if skill is None:
                return await _fail(f"没有这个技能：{name}", status_code=404)
            return await _ok({"name": skill.name, "body": skill.body, "chars": skill.chars})
        except Exception as exc:
            return await _fail(f"读取技能失败：{exc}")

    # ------------------------------------------------------------ 动作记录
    async def audit_log(self) -> Any:
        try:
            records = self._plugin._audit.load()[-120:]
            records.reverse()
            return await _ok({"records": records, "total": len(records)})
        except Exception as exc:
            return await _fail(f"读取动作记录失败：{exc}")

    async def audit_clear(self) -> Any:
        try:
            self._plugin._audit.clear()
            return await _ok({"ok": True})
        except Exception as exc:
            return await _fail(f"清空失败：{exc}")
