"""为你而在的认知：给 AstrBot 一套可被模型真正调用的"动手"能力。

工具清单（每个都有独立开关，默认只开安全的那些）：
- 工作区：`toolbox_info` 看概况
- 文件：`fs_list` / `fs_read` / `fs_write` / `fs_search`
- 网络：`web_fetch`（抓正文）/ `web_search`（Tavily）/ `download`（下载进工作区）
- 命令：`shell_run`（**默认关闭**，白名单 + 超时 + 工作目录锁死）

设计原则：一切文件操作都在 `plugin_data/<plugin>/workspace` 之内（路径监狱），
网络拒绝内网地址（防 SSRF），命令默认关闭且拒绝高危程序名。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from . import PLUGIN_DISPLAY, PLUGIN_NAME, PLUGIN_VERSION
from . import shell as shell_mod
from . import skills as skills_mod
from . import tasks as tasks_mod
from . import web as web_mod
from . import notes as notes_mod
from . import notify as notify_mod
from . import archive as archive_mod
from . import image_ops as image_mod
from . import parallel as parallel_mod
from . import pdfgen as pdf_mod
from . import page_api as page_api_mod
from . import policy as policy_mod
from . import workspace_policy as ws_policy
from . import qq_actions as qq_mod
from . import delegate as delegate_mod
from . import exec as exec_mod
from . import fstools as fstools_mod
from . import goals as goals_mod
from . import office as office_mod
from .audit import AuditLog, Quota
from .workspace import Workspace, WorkspaceError, human_size, safe_filename_from_url

_TOOL_TITLES = {
    "info": "工作区概况",
    "fs_list": "列目录",
    "fs_read": "读文件",
    "fs_write": "写文件",
    "fs_search": "搜内容",
    "web_fetch": "抓网页",
    "web_search": "联网搜索",
    "learn": "汲取经验",
    "doc_read": "读文档",
    "doc_write": "写文档",
    "send_file": "发送文件",
    "doc_export": "导出 PDF",
    "skill_list": "技能清单",
    "skill": "取技能流程",
    "parallel": "并行执行",
    "task_wait": "等任务完成",
    "edit": "精确编辑",
    "glob": "模式查找",
    "goal": "长期目标",
    "delegate": "子代理",
    "delegate_list": "专家清单",
    "image": "处理图片",
    "archive": "打包解包",
    "poke": "戳一戳",
    "like": "点赞",
    "audit": "调用审计",
    "python": "跑脚本",
    "task_start": "启动后台任务",
    "task_status": "查后台任务",
    "task_stop": "停后台任务",
    "download": "下载文件",
    "shell_run": "执行命令",
}


class CognitionPlugin(Star):
    """插件主体：只负责把工具暴露给模型，并把每次调用记录在案。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config if isinstance(config, dict) else {}
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root = self.data_dir / "workspace"
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._workspace: Workspace | None = None
        # P1-3：审计同时落盘（audit.jsonl），重启后仍能回答"你之前动过什么"
        self._audit = AuditLog(capacity=80, path=self.data_dir / "audit.jsonl")
        self._quota = Quota()
        logger.info("[Toolbox] %s %s 已加载，工作区=%s", PLUGIN_DISPLAY, PLUGIN_VERSION, self.workspace_root)

    # ------------------------------------------------------------ 生命周期
    async def initialize(self) -> None:
        try:
            page_api_mod.CognitionWebApi(self).mount(self.context)
        except Exception as exc:  # 面板失败不能影响插件本体
            logger.warning("[Toolbox] 面板接口挂载失败（插件仍可用）: %s", exc)
        try:
            written = skills_mod.ensure_defaults(self.skills_root)
            if written:
                logger.info("[Toolbox] 已写入 %s 个内置技能示例到 %s", written, self.skills_root)
            loaded = self._audit.load()
            if loaded:
                logger.info("[Toolbox] 已从 audit.jsonl 载入 %s 条历史审计", loaded)
        except Exception as exc:
            logger.warning("[Toolbox] 载入历史审计失败（忽略）: %s", exc)
        try:
            (self.workspace_root / "README.txt").write_text(
                "这是「为你动手的工具箱」的工作区。\n"
                "模型只能在这个目录里读写文件、下载文件；外面的东西它碰不到。\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[Toolbox] 初始化工作区说明失败: %s", exc)

    async def terminate(self) -> None:
        manager = getattr(self, "_task_manager", None)
        if manager is not None:
            try:
                await manager.shutdown()
                logger.info("[Toolbox] 后台任务已清理")
            except Exception as exc:
                logger.warning("[Toolbox] 清理后台任务失败: %s", exc)
        logger.info("[Toolbox] %s 已停止", PLUGIN_DISPLAY)

    # ------------------------------------------------------------ 配置读取
    def _get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.config
        for part in str(dotted or "").split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def _bool(self, dotted: str, default: bool = True) -> bool:
        value = self._get(dotted, default)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on", "开", "开启", "是"):
            return True
        if text in ("0", "false", "no", "off", "关", "关闭", "否"):
            return False
        return bool(default)

    def _int(self, dotted: str, default: int) -> int:
        try:
            return int(self._get(dotted, default))
        except (TypeError, ValueError):
            return int(default)

    def _float(self, dotted: str, default: float) -> float:
        try:
            return float(self._get(dotted, default))
        except (TypeError, ValueError):
            return float(default)

    def _str(self, dotted: str, default: str = "") -> str:
        value = self._get(dotted, default)
        return str(value if value is not None else default)

    # ------------------------------------------------------------ 工作区 / 配额 / 审计
    @property
    def workspace(self) -> Workspace:
        if self._workspace is None:
            self._workspace = Workspace(
                self.workspace_root,
                max_read_bytes=self._int("fs.max_read_bytes", 262144),
                max_write_bytes=self._int("fs.max_write_bytes", 1048576),
                max_download_bytes=self._int("download.max_bytes", 52428800),
                max_list_entries=self._int("fs.max_list_entries", 200),
                allow_write=self._bool("fs.allow_write", True),
            )
        return self._workspace

    @property
    def delegate_budget(self) -> delegate_mod.CallBudget:
        """子代理的每日调用预算（防成本失控）。"""
        if getattr(self, "_delegate_budget", None) is None:
            self._delegate_budget = delegate_mod.CallBudget(
                daily_limit=self._int("delegate.daily_limit", 30)
            )
        return self._delegate_budget

    def _experts(self) -> list[delegate_mod.Expert]:
        """从配置读专家列表（agents 是 JSON 数组字符串）。"""
        try:
            raw = self.config.get("delegate.agents", "")
        except Exception:
            raw = ""
        return [e for e in delegate_mod.parse_experts(raw) if e.enabled]

    @property
    def goals(self) -> goals_mod.GoalStore:
        """长期目标库（goals.json，跨会话保留）。"""
        if getattr(self, "_goal_store", None) is None:
            self._goal_store = goals_mod.GoalStore(
                self.data_dir / "goals.json",
                max_goals=self._int("goals.max_open", 50),
            )
        return self._goal_store

    @property
    def skills_root(self) -> Path:
        """技能目录：plugin_data/<插件>/skills（人放文件，模型只读）。"""
        path = self.data_dir / "skills"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def principal_for(self, event: Any) -> policy_mod.Principal:
        """当前说话人的身份等级（认不出 → 访客）。"""
        return policy_mod.resolve_principal(self, event)

    def plan_for(self, event: Any) -> ws_policy.WorkspacePlan:
        """按配置与会话算工作区计划（模式 + 访问级别 + 允许的根）。"""
        return ws_policy.resolve_plan(self, event, principal=self.principal_for(event))

    def workspace_for(self, event: Any) -> Workspace:
        """取"这次对话"该用的工作区。配置异常时安全回退到共用工作区。"""
        try:
            plan = self.plan_for(event)
            return Workspace(
                plan.root,
                max_read_bytes=self._int("fs.max_read_bytes", 262144),
                max_write_bytes=self._int("fs.max_write_bytes", 1048576),
                max_download_bytes=self._int("download.max_bytes", 52428800),
                max_list_entries=self._int("fs.max_list_entries", 200),
                allow_write=self._bool("fs.allow_write", True),
                access=plan.access,
                extra_roots=plan.extra_roots,
            )
        except Exception as exc:
            logger.warning("[Toolbox] 解析工作区失败，回退到共用工作区: %s", exc)
            return self.workspace

    def _simulation_on(self) -> bool:
        """试运行：只回话，不真的改东西。"""
        try:
            return bool(self.config.bool("simulation.enabled", False))
        except Exception:
            return False

    def _sim_block(self, action: str, detail: str = "") -> str:
        """试运行下拦下写操作，返回给模型看的说明（空串=放行）。"""
        if not self._simulation_on():
            return ""
        self._note_call(action, False, "试运行未执行")
        return f"（试运行，未执行）{detail or action}：现在只演不做，关闭试运行后会真正执行。"

    def _allow(self, event: Any, action: str) -> str:
        """权限门：按身份等级判断这个动作能不能做。返回空串表示放行。"""
        principal = self.principal_for(event)
        if policy_mod.can(principal, action):
            return ""
        self._note_call(action, False, f"权限不足：{principal.level}")
        return policy_mod.deny_reason(principal, action)

    @property
    def tasks(self) -> tasks_mod.TaskManager:
        """后台任务表（首次使用时按当前配置创建）。"""
        manager = getattr(self, "_task_manager", None)
        if manager is None:
            manager = tasks_mod.TaskManager(
                self.workspace_root,
                max_concurrent=self._int("tasks.max_concurrent", 2),
                max_age_seconds=self._float("tasks.max_age_seconds", 1800.0),
                log_tail_chars=self._int("tasks.log_tail_chars", 2000),
                python=sys.executable or None,
            )
            self._task_manager = manager
        return manager

    def _action_left(self, kind: str) -> int:
        """平台动作（戳/赞）的每日余额。limit<=0 表示不限。"""
        limit = self._int("actions.daily_limit", 20)
        if limit <= 0:
            return -1
        day = time.strftime("%Y-%m-%d", time.localtime())
        used = getattr(self, "_action_counts", {}).get(f"{day}:{kind}", 0)
        return max(0, limit - used)

    def _note_action(self, kind: str) -> None:
        counts = getattr(self, "_action_counts", None)
        if counts is None:
            counts = {}
            self._action_counts = counts
        day = time.strftime("%Y-%m-%d", time.localtime())
        key = f"{day}:{kind}"
        counts[key] = counts.get(key, 0) + 1
        # 只留今天的键，防止无限增长
        for old in [k for k in counts if not k.startswith(day)]:
            counts.pop(old, None)

    def _bot_self_id(self) -> str:
        """尽力取机器人自己的 QQ 号（取不到就空串，不影响默认目标）。"""
        for getter in ("get_self_id",):
            try:
                value = getattr(self.context, getter, None)
                if callable(value):
                    return str(value() or "")
            except Exception:
                pass
        return ""

    def _quota_left(self) -> int:
        """今天还剩多少次工具调用额度（0 = 用完了，-1 = 不限制）。"""
        return self._quota.left(self._int("limits.daily_calls", 200))

    def _note_call(self, tool: str, ok: bool, detail: str = "") -> None:
        self._quota.bump(tool)
        self._audit.note(tool, ok, detail)

    async def _remember_action(self, event: Any, *, kind: str, text: str, meta: dict | None = None) -> None:
        """把"我做了什么"转告人格（认知 → 人格 → 记忆）。失败只记日志，不影响工具结果。"""
        try:
            result = await notify_mod.notify_persona(
                self.context, event, kind=kind, text=text, meta=meta or {}
            )
            if not result.get("ok"):
                logger.debug("[Toolbox] 记忆转写未达成：%s", result.get("reason"))
        except Exception as exc:
            logger.debug("[Toolbox] 记忆转写异常（忽略）: %s", exc)

    def _owner_denied(self, event: Any, *, tool: str = "script") -> str:
        """owner 门禁：名单为空 = 谁都不许（绝不等于所有人）。返回空串表示放行。"""
        sender = ""
        try:
            sender = str(event.get_sender_id() or "")
        except Exception:
            sender = ""
        owners = exec_mod.parse_owners(self._str("exec.owner_ids", ""))
        if exec_mod.is_owner(sender, owners):
            return ""
        self._note_call(tool, False, f"非 owner：{sender or '未知'}")
        return "这个工具只对主人开放（要写代码跑任务的权限不能给所有人）。"

    def _gate(self, tool: str, enabled_key: str) -> str:
        """统一门禁：总开关 → 分项开关 → 每日配额。返回空串表示放行，否则返回拒绝文案。"""
        if not self._bool("general.enabled", True):
            return "工具箱总开关是关闭的，现在不能动手。"
        if not self._bool(enabled_key, True):
            names = {**{"shell_run": "执行命令"}, **{f"{k}": v for k, v in _TOOL_TITLES.items()}}
            return f"「{names.get(tool, tool)}」这个工具被配置关掉了。"
        left = self._quota_left()
        if left == 0:
            return f"今天动手的次数用完了（上限 {self._int('limits.daily_calls', 200)} 次），明天再弄。"
        return ""

    # ------------------------------------------------------------ 工具：概况
    @filter.llm_tool(name="qq_set_signature")
    async def qq_set_signature_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """改你 QQ 上的个性签名（对外可见，别人点开你资料能看到）。

        什么时候用：心情明显变了、想留一句话、或者对方问到你的签名。
        不要用：每次聊天都改、或者写长句子（签名要短，像真人随手写的那种）。

        Args:
            text(string): 新的签名文本，60 字以内。
        """
        block = self._gate("signature", "actions.signature_enabled")
        if block:
            return block
        deny = self._allow(event, "signature")
        if deny:
            return deny

        text = str(kwargs.get("text") or "").strip()
        if not text:
            self._note_call("signature", False, "空文本")
            return "签名文本不能为空。"
        if len(text) > 60:
            self._note_call("signature", False, f"超长 {len(text)}")
            return f"签名太长（{len(text)} 字），最多 60 字，改短一点。"

        sim = self._sim_block("signature", "改签名")
        if sim:
            return sim

        bot = getattr(event, "bot", None)
        caller = getattr(bot, "call_action", None)
        if not callable(caller):
            self._note_call("signature", False, "平台不支持 call_action")
            return "这个平台适配器不支持主动动作，改不了签名。"
        try:
            await caller("set_self_longnick", long_nick=text)
        except Exception as exc:
            self._note_call("signature", False, str(exc))
            return f"改签名失败：{type(exc).__name__}: {exc}（如实说没改成）"

        self._note_call("signature", True, text[:20])
        await self._remember_action(event, kind="signature", text=f"把签名改成了「{text}」")
        return f"签名已改成「{text}」。"

    @filter.llm_tool(name="qq_poke")
    async def qq_poke_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """戳对方一下（QQ 的「拍一拍」）。

        什么时候用：对方很久没回话、你想引起注意、或者语气上想撒娇/催促。
        不要用：正经回答问题、或者对方明显有事时反复戳。

        Args:
            (无参数：戳谁从当前会话自动取)
        """
        block = self._gate("poke", "actions.poke_enabled")
        if block:
            return block
        deny = self._allow(event, "poke")
        if deny:
            return deny
        sim = self._sim_block("poke", "戳一戳")
        if sim:
            return sim

        sender = ""
        try:
            sender = str(event.get_sender_id() or "").strip()
        except Exception:
            sender = ""
        if not sender or not sender.isdigit():
            self._note_call("poke", False, "拿不到对方 ID")
            return "拿不到对方 QQ 号，戳不了。"

        group_id = ""
        try:
            group_id = str(event.get_group_id() or "").strip()
        except Exception:
            group_id = ""

        bot = getattr(event, "bot", None)
        caller = getattr(bot, "call_action", None)
        if not callable(caller):
            self._note_call("poke", False, "平台不支持 call_action")
            return "这个平台适配器不支持主动动作，戳不了。"

        args: dict[str, Any] = {"user_id": int(sender)}
        if group_id and group_id.isdigit():
            args["group_id"] = int(group_id)
        try:
            await caller("send_poke", **args)
        except Exception as exc:
            self._note_call("poke", False, str(exc))
            return f"戳一戳失败：{type(exc).__name__}: {exc}（如实说没戳成，别硬说戳到了）"

        self._note_call("poke", True, "已戳")
        await self._remember_action(event, kind="poke", text="戳了对方一下")
        return "已戳对方一下。"

    @filter.llm_tool(name="toolbox_info")
    async def toolbox_info_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """查看工作区现状与可用工具（文件放哪、还有多少空间、哪些工具开着）。

        在你准备读写文件、下载东西之前先看一眼，避免瞎猜路径。
        """
        block = self._gate("info", "general.enabled")
        if block:
            return block
        try:
            data = self.workspace_for(event).summary()
            lines = [
                f"工作区目录：{self.workspace_root}",
                f"里面已有 {data['files']} 个文件 / {data['dirs']} 个子目录，共 {human_size(int(data['bytes']))}",
                f"磁盘剩余：{human_size(int(data['free'])) if int(data['free']) >= 0 else '未知'}",
                "可用工具：" + "、".join(
                    f"{_TOOL_TITLES[k]}({'开' if self._bool(v, True) else '关'})"
                    for k, v in (
                        ("fs_list", "fs.enabled"),
                        ("fs_read", "fs.enabled"),
                        ("fs_write", "fs.enabled"),
                        ("fs_search", "fs.enabled"),
                        ("web_fetch", "web.fetch_enabled"),
                        ("web_search", "web.search_enabled"),
                        ("download", "download.enabled"),
                        ("shell_run", "shell.enabled"),
                    )
                ),
                f"今日已动手：{self._quota.used('total')} 次"
                + (f"（上限 {self._int('limits.daily_calls', 200)}）" if self._int("limits.daily_calls", 200) > 0 else ""),
                "最近调用：" + ("；".join(e.as_line() for e in self._audit.recent(3)) or "（还没有）"),
            ]
            self._note_call("info", True)
            return "\n".join(lines)
        except Exception as exc:
            self._note_call("info", False, str(exc))
            return f"查看工作区失败：{exc}"

    # ------------------------------------------------------------ 工具：文件
    @filter.llm_tool(name="toolbox_fs_list")
    async def toolbox_fs_list_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """列出工作区里某个目录的内容（文件名、大小、是否目录）。

        省略 path 就列根目录。看子目录时传相对路径，例如 "notes"。

        Args:
            path(string): 相对目录路径，省略或 "." 表示工作区根目录。
        """
        block = self._gate("fs_list", "fs.enabled")
        if block:
            return block
        path = str(kwargs.get("path") or "").strip()
        try:
            rel, items = self.workspace_for(event).list_dir(path)
            if not items:
                self._note_call("fs_list", True, rel)
                return f"目录「{rel}」是空的。"
            lines = [f"目录「{rel}」共 {len(items)} 项："]
            for item in items:
                mark = "/" if item.is_dir else ""
                size = "" if item.is_dir else f"  {human_size(item.size)}"
                lines.append(f"  {item.path}{mark}{size}")
            self._note_call("fs_list", True, rel)
            return "\n".join(lines)
        except WorkspaceError as exc:
            self._note_call("fs_list", False, str(exc))
            return f"列目录失败：{exc}"
        except Exception as exc:
            self._note_call("fs_list", False, str(exc))
            return f"列目录出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_fs_read")
    async def toolbox_fs_read_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """读取工作区里的文本文件内容。

        大文件可以用 head / tail 只看开头或结尾的若干行。

        Args:
            path(string): 相对文件路径，例如 "notes/todo.md"。
            head(number): 只看前 N 行，0 表示不限。
            tail(number): 只看最后 N 行，0 表示不限。
        """
        block = self._gate("fs_read", "fs.enabled")
        if block:
            return block
        path = str(kwargs.get("path") or "").strip()
        try:
            head = int(kwargs.get("head") or 0)
        except (TypeError, ValueError):
            head = 0
        try:
            tail = int(kwargs.get("tail") or 0)
        except (TypeError, ValueError):
            tail = 0
        try:
            text, size = self.workspace_for(event).read_text(path, head=head, tail=tail)
            self._note_call("fs_read", True, path)
            if not text.strip():
                return f"文件「{path}」是空的（{size} 字节）。"
            return f"文件「{path}」（{human_size(size)}）：\n{text}"
        except WorkspaceError as exc:
            self._note_call("fs_read", False, str(exc))
            return f"读文件失败：{exc}"
        except Exception as exc:
            self._note_call("fs_read", False, str(exc))
            return f"读文件出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_fs_write")
    async def toolbox_fs_write_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把文本写进工作区里的文件（自动建目录；默认覆盖同名文件）。

        Args:
            path(string): 相对文件路径，例如 "notes/2026-10-25.md"。
            content(string): 要写入的完整文本。
            overwrite(boolean): 是否覆盖已存在的文件，默认 true。
        """
        block = self._gate("fs_write", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "write")
        if deny:
            return deny
        sim = self._sim_block("toolbox_fs_write", "写文件")
        if sim:
            return sim
        path = str(kwargs.get("path") or "").strip()
        content = str(kwargs.get("content") or "")
        overwrite_raw = kwargs.get("overwrite", True)
        overwrite = overwrite_raw if isinstance(overwrite_raw, bool) else str(overwrite_raw).lower() not in ("0", "false", "否")
        try:
            saved = self.workspace_for(event).write_text(path, content, overwrite=overwrite)
            self._note_call("fs_write", True, saved)
            return f"已写入「{saved}」（{human_size(len(content.encode('utf-8')))}）。"
        except WorkspaceError as exc:
            self._note_call("fs_write", False, str(exc))
            return f"写文件失败：{exc}"
        except Exception as exc:
            self._note_call("fs_write", False, str(exc))
            return f"写文件出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_fs_search")
    async def toolbox_fs_search_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """在工作区的文本文件里按正则搜索内容，返回命中的文件、行号与那一行。

        Args:
            pattern(string): 正则表达式，例如 "显卡|5090"。
            path(string): 搜索起点（相对路径），省略为根目录。
            glob(string): 只搜匹配的文件名，例如 "*.md"，默认 "*"。
            max_hits(number): 最多返回多少条命中，默认 30。
        """
        block = self._gate("fs_search", "fs.enabled")
        if block:
            return block
        pattern = str(kwargs.get("pattern") or "").strip()
        path = str(kwargs.get("path") or "").strip()
        glob = str(kwargs.get("glob") or "*").strip() or "*"
        try:
            max_hits = int(kwargs.get("max_hits") or 30)
        except (TypeError, ValueError):
            max_hits = 30
        try:
            hits, scanned = self.workspace_for(event).search_text(
                pattern, relative=path, glob=glob, max_hits=max(1, min(100, max_hits))
            )
            self._note_call("fs_search", True, pattern)
            if not hits:
                return f"扫了 {scanned} 个文件，没找到匹配「{pattern}」的内容。"
            lines = [f"在 {scanned} 个文件里找到 {len(hits)} 处匹配："]
            lines += [f"  {rel}:{line_no}: {text}" for rel, line_no, text in hits]
            return "\n".join(lines)
        except WorkspaceError as exc:
            self._note_call("fs_search", False, str(exc))
            return f"搜索失败：{exc}"
        except Exception as exc:
            self._note_call("fs_search", False, str(exc))
            return f"搜索出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：网络
    @filter.llm_tool(name="toolbox_web_fetch")
    async def toolbox_web_fetch_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """打开一个网址，抓取它的正文文本（适合读文章、文档页、公告）。

        抓不到就如实说；不要把没读到的内容当成读到。

        Args:
            url(string): 完整网址（http/https）。
            max_chars(number): 最多返回多少字，默认 4000。
        """
        block = self._gate("web_fetch", "web.fetch_enabled")
        if block:
            return block
        url = str(kwargs.get("url") or "").strip()
        try:
            max_chars = int(kwargs.get("max_chars") or self._int("web.max_chars", 4000))
        except (TypeError, ValueError):
            max_chars = self._int("web.max_chars", 4000)
        try:
            result = await web_mod.fetch_text(
                url,
                timeout=self._float("web.timeout_seconds", 20.0),
                max_bytes=self._int("web.max_bytes", 2097152),
                max_chars=max(300, min(20000, max_chars)),
                user_agent=self._str("web.user_agent", ""),
            )
            self._note_call("web_fetch", True, result.url)
            head = f"标题：{result.title}\n" if result.title else ""
            return f"{head}网址：{result.url}（HTTP {result.status}，读到 {human_size(result.bytes_read)}）\n\n{result.text}"
        except web_mod.WebError as exc:
            self._note_call("web_fetch", False, str(exc))
            return f"抓网页失败：{exc}"
        except Exception as exc:
            self._note_call("web_fetch", False, str(exc))
            return f"抓网页出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_web_search")
    async def toolbox_web_search_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """联网搜索当前信息（时效性内容、新闻、价格、事实核对）。

        搜不到就说没搜到，不要编。

        Args:
            query(string): 搜索词。
            max_results(number): 返回几条结果，默认 5。
        """
        block = self._gate("web_search", "web.search_enabled")
        if block:
            return block
        query = str(kwargs.get("query") or "").strip()
        try:
            count = int(kwargs.get("max_results") or self._int("web.search_results", 5))
        except (TypeError, ValueError):
            count = self._int("web.search_results", 5)
        try:
            text = await web_mod.tavily_search(
                query,
                api_key=self._str("web.tavily_api_key", "") or self._str("web.search_api_key", ""),
                max_results=max(1, min(10, count)),
                timeout=self._float("web.timeout_seconds", 20.0),
            )
            self._note_call("web_search", True, query)
            return text
        except web_mod.WebError as exc:
            self._note_call("web_search", False, str(exc))
            return f"搜索失败：{exc}"
        except Exception as exc:
            self._note_call("web_search", False, str(exc))
            return f"搜索出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_download")
    async def toolbox_download_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把一个网址的文件下载到工作区（图片/文档/压缩包等），并返回保存路径。

        Args:
            url(string): 文件的完整网址。
            path(string): 保存到工作区的相对路径；省略则自动用网址里的文件名。
        """
        block = self._gate("download", "download.enabled")
        if block:
            return block
        deny = self._allow(event, "download")
        if deny:
            return deny
        url = str(kwargs.get("url") or "").strip()
        target = str(kwargs.get("path") or "").strip()
        allowed_ext = self._str("download.allow_extensions", "")
        try:
            data, ctype, _ = await web_mod.download(
                url,
                timeout=self._float("web.timeout_seconds", 20.0) + 10,
                max_bytes=self._int("download.max_bytes", 52428800),
                user_agent=self._str("web.user_agent", ""),
            )
            if not target:
                target = "downloads/" + safe_filename_from_url(url)
            if allowed_ext:
                suffix = Path(target).suffix.lower().lstrip(".")
                allowed = {x.strip().lower().lstrip(".") for x in allowed_ext.replace("，", ",").split(",") if x.strip()}
                if allowed and suffix not in allowed:
                    return f"这个类型不允许下载（{suffix or '无扩展名'}）；允许：{'、'.join(sorted(allowed))}"
            saved = self.workspace_for(event).save_bytes(target, data)
            self._note_call("download", True, saved)
            await self._remember_action(
                event, kind="download",
                text=f"我下载了一个文件，存到 {saved}（{human_size(len(data))}）。",
                meta={"path": saved, "url": url},
            )
            extra = f"（{ctype}）" if ctype else ""
            return f"已下载到「{saved}」，{human_size(len(data))}{extra}。"
        except (web_mod.WebError, WorkspaceError) as exc:
            self._note_call("download", False, str(exc))
            return f"下载失败：{exc}"
        except Exception as exc:
            self._note_call("download", False, str(exc))
            return f"下载出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：汲取经验
    @filter.llm_tool(name="toolbox_learn")
    async def toolbox_learn_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把一篇网页（或一次搜索的首条结果）读进来，整理成一条"经验"存到工作区 notes/ 里。

        抓不到就如实说抓不到；不要凭记忆编造内容。

        Args:
            url(string): 要读的网址；与 query 二选一。
            query(string): 搜索词；没有 url 时用它搜索并取首条结果。
            title(string): 自己给这条经验起个标题，省略则从网页标题推断。
        """
        block = self._gate("learn", "web.fetch_enabled")
        if block:
            return block
        deny = self._allow(event, "write")
        if deny:
            return deny
        url = str(kwargs.get("url") or "").strip()
        query = str(kwargs.get("query") or "").strip()
        title = str(kwargs.get("title") or "").strip()
        if not url and not query:
            return "要给我一个网址或搜索词，不然不知道读什么。"
        used_query = ""
        body = ""
        page_title = title
        source = url
        try:
            if not url:
                found = await web_mod.tavily_search(
                    query,
                    api_key=self._str("web.tavily_api_key", "") or self._str("web.search_api_key", ""),
                    max_results=3,
                    timeout=self._float("web.timeout_seconds", 20.0),
                )
                used_query = query
                match = None
                for line in found.splitlines():
                    if line.strip().startswith("http"):
                        match = line.strip()
                        break
                if match:
                    source = match
                else:
                    # 没有可抓的链接：把搜索摘要本身作为经验内容
                    body = found
                    page_title = page_title or f"关于「{query}」的搜索摘要"
            if source and not body:
                result = await web_mod.fetch_text(
                    source,
                    timeout=self._float("web.timeout_seconds", 20.0),
                    max_bytes=self._int("web.max_bytes", 2097152),
                    max_chars=self._int("notes.max_chars", 6000),
                    user_agent=self._str("web.user_agent", ""),
                )
                body = result.text
                page_title = page_title or result.title
            if not str(body).strip():
                self._note_call("learn", False, "抓到的正文为空")
                return f"抓到了页面但没读到正文（{source}），这条经验没存。"

            filename = notes_mod.note_filename(title=page_title or "", url=source)
            note = notes_mod.compose_note(
                title=page_title or "",
                url=source,
                body=body,
                query=used_query,
                max_body_chars=self._int("notes.max_chars", 6000),
            )
            directory = self._str("notes.dir", "notes").strip() or "notes"
            saved = self.workspace_for(event).write_text(f"{directory}/{filename}", note)
            excerpt, truncated = notes_mod.summarize_for_reply(note)
            self._note_call("learn", True, saved)
            await self._remember_action(
                event,
                kind="learn",
                text=f"我读完了一篇内容（{page_title or '未命名'}），整理成经验存到 {saved}。"
                     f"要点：{excerpt[:160]}",
                meta={"path": saved, "source": source, "query": used_query},
            )
            head = f"已记下一条经验：「{saved}」（{human_size(len(note.encode('utf-8')))}）"
            if used_query:
                head += f"\n来源：搜索「{used_query}」→ {source}"
            else:
                head += f"\n来源：{source}"
            return f"{head}\n\n要点：{excerpt}" + ("\n（要点已截断，完整内容在文件里）" if truncated else "")
        except (web_mod.WebError, WorkspaceError) as exc:
            self._note_call("learn", False, str(exc))
            return f"没记成：{exc}"
        except Exception as exc:
            self._note_call("learn", False, str(exc))
            return f"记这条经验时出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：Office
    @filter.llm_tool(name="toolbox_doc_read")
    async def toolbox_doc_read_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """读工作区里的 Office 文档（.docx 段落与表格 / .xlsx 工作表内容）并返回文本摘要。

        Args:
            path(string): 相对文件路径，例如 "reports/summary.docx"。
        """
        block = self._gate("doc_read", "fs.enabled")
        if block:
            return block
        path = str(kwargs.get("path") or "").strip()
        try:
            target = self.workspace_for(event).resolve(path, must_exist=True)
            suffix = target.suffix.lower()
            if suffix == ".docx":
                result = office_mod.read_docx(
                    target,
                    max_paragraphs=self._int("office.max_paragraphs", 200),
                    max_chars=self._int("office.max_chars", 8000),
                )
            elif suffix in (".xlsx", ".xlsm"):
                result = office_mod.read_xlsx(
                    target,
                    max_rows=self._int("office.max_rows", 40),
                    max_chars=self._int("office.max_chars", 8000),
                )
            elif suffix == ".pptx":
                result = office_mod.read_pptx(
                    target,
                    max_slides=self._int("office.max_slides", 30),
                    max_chars=self._int("office.max_chars", 8000),
                )
            else:
                return (
                    f"只支持 .docx / .xlsx / .pptx，这个是 {suffix or '无扩展名'}；"
                    "普通文本请用 toolbox_fs_read。"
                )
            self._note_call("doc_read", True, path)
            if not result.text.strip():
                return f"「{path}」里没有读到内容（{result.kind}）。"
            summary = f"「{path}」（{result.kind}，"
            if result.kind == "docx":
                summary += f"{result.paragraphs} 段"
            elif result.kind == "pptx":
                summary += f"{result.paragraphs} 页"
            else:
                summary += f"{result.tables} 个工作表、{result.paragraphs} 行"
            summary += f"）：\n{result.text}"
            return summary
        except (office_mod.OfficeError, WorkspaceError) as exc:
            self._note_call("doc_read", False, str(exc))
            return f"读文档失败：{exc}"
        except Exception as exc:
            self._note_call("doc_read", False, str(exc))
            return f"读文档出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_doc_write")
    async def toolbox_doc_write_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """写一份文档文件（.docx / .txt / .md / .xlsx）。

        **什么时候用**：对方说「做个文档」「写个 Word」「做份报告 / 纪要 / 总结 / 汇报」
        「存成文件」「帮我整理成文档」—— 这些都是**写文件**，用这个工具。
        **不要**用拍照/画图/生图来应付这类请求，那只会得到一张图片，不是文件。

        **怎么用**：.docx 按行写段落（一行一段）；.xlsx 用 ` | ` 分隔列（逗号分隔会报错）。
        写完用 `toolbox_send_file` 把文件发出去，别只说「做好了」。

        Args:
            path(string): 相对文件路径，扩展名决定格式，例如 "notes/report.docx"。
            content(string): docx 写正文（一行一段，"- " 开头为列表）；xlsx 写表格（每行用 | 分隔单元格）；
                pptx 写幻灯片大纲（单独一行 --- 分页，# 开头是页标题，- 开头是要点）。
            title(string): docx 的标题 / pptx 的封面标题，可省略。
            subtitle(string): pptx 封面的副标题，可省略。
            sheet_name(string): xlsx 的工作表名，可省略。
        """
        block = self._gate("doc_write", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "office")
        if deny:
            return deny
        sim = self._sim_block("toolbox_doc_write", "生成文档")
        if sim:
            return sim
        if not self._bool("fs.allow_write", True):
            return "工作区现在是只读的，不能写文档。"
        path = str(kwargs.get("path") or "").strip()
        content = str(kwargs.get("content") or "")
        title = str(kwargs.get("title") or "").strip()
        sheet_name = str(kwargs.get("sheet_name") or "Sheet1").strip() or "Sheet1"
        subtitle = str(kwargs.get("subtitle") or "").strip()
        if not path:
            return "要给个文件名，例如 notes/report.docx。"
        try:
            target = self.workspace_for(event).resolve(path)
            suffix = target.suffix.lower()
            if suffix == ".docx":
                lines = [line.rstrip() for line in content.splitlines()]
                count = office_mod.write_docx(target, title=title, lines=lines)
                size = target.stat().st_size
                self._note_call("doc_write", True, path)
                await self._remember_action(
                    event, kind="doc_write",
                    text=f"我做了一份文档 {self.workspace_for(event).relpath(target)}（docx，{count} 段）。",
                    meta={"path": self.workspace_for(event).relpath(target), "format": "docx"},
                )
                return f"已生成「{self.workspace_for(event).relpath(target)}」（docx，{count} 段，{human_size(size)}）。"
            if suffix in (".xlsx", ".xlsm"):
                rows = office_mod.parse_rows(content)
                if not rows:
                    return "表格内容是空的，没写。"
                count = office_mod.write_xlsx(target, rows, sheet_name=sheet_name)
                size = target.stat().st_size
                self._note_call("doc_write", True, path)
                await self._remember_action(
                    event, kind="doc_write",
                    text=f"我做了一份表格 {self.workspace_for(event).relpath(target)}（xlsx，{count} 行）。",
                    meta={"path": self.workspace_for(event).relpath(target), "format": "xlsx"},
                )
                return f"已生成「{self.workspace_for(event).relpath(target)}」（xlsx，{count} 行，{human_size(size)}）。"
            if suffix == ".pptx":
                slides = office_mod.parse_slides(content)
                if not slides:
                    return "幻灯片内容是空的，没写。"
                # P1-4：副标题用独立参数；旧调用若把内容塞在 sheet_name 里仍兼容
                _subtitle = subtitle or (sheet_name if sheet_name != "Sheet1" else "")
                count = office_mod.write_pptx(
                    target, slides, title=title, subtitle=_subtitle
                )
                size = target.stat().st_size
                self._note_call("doc_write", True, path)
                await self._remember_action(
                    event, kind="doc_write",
                    text=f"我做了一份幻灯片 {self.workspace_for(event).relpath(target)}（pptx，{count} 页正文）。",
                    meta={"path": self.workspace_for(event).relpath(target), "format": "pptx"},
                )
                return (
                    f"已生成「{self.workspace_for(event).relpath(target)}」（pptx，{count} 页正文"
                    + ("，含封面" if title else "") + f"，{human_size(size)}）。"
                )
            return (
                f"只支持 .docx / .xlsx / .pptx，这个是 {suffix or '无扩展名'}；"
                "纯文本请用 toolbox_fs_write。"
            )
        except (office_mod.OfficeError, WorkspaceError) as exc:
            self._note_call("doc_write", False, str(exc))
            return f"写文档失败：{exc}"
        except Exception as exc:
            self._note_call("doc_write", False, str(exc))
            return f"写文档出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：审计
    @filter.llm_tool(name="toolbox_audit")
    async def toolbox_audit_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """查看最近调用过哪些工具、成功还是失败（用于自查"我到底动过什么"）。

        Args:
            limit(number): 最多看多少条，默认 15。
        """
        try:
            count = int(kwargs.get("limit") or 15)
        except (TypeError, ValueError):
            count = 15
        entries = self._audit.recent(max(1, min(60, count)))
        stats = self._audit.stats()
        if not entries:
            return "还没有调用记录。"
        lines = [f"最近 {len(entries)} 次调用（累计 {stats['total']} 次：成功 {stats['ok']} / 失败 {stats['failed']}）："]
        lines += [f"  {entry.as_line()}" for entry in entries]
        if stats["by_tool"]:
            lines.append("按工具：" + "、".join(f"{k}×{v}" for k, v in sorted(stats["by_tool"].items())))
        return "\n".join(lines)

    # ------------------------------------------------------------ 工具：发文件
    @filter.llm_tool(name="toolbox_send_file")
    async def toolbox_send_file_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把工作区里的一个文件发到当前会话（对方能直接收到这个文件）。

        用于"把这个文档发我""把刚做的 PPT 发过来""把下载的东西发出来"这类要求。

        Args:
            path(string): 工作区内的相对路径，例如 "reports/周报.docx"。
            caption(string): 随文件一起说的一句话，可省略。
        """
        block = self._gate("send_file", "send.enabled")
        if block:
            return block
        deny = self._allow(event, "send")
        if deny:
            return deny
        path = str(kwargs.get("path") or "").strip()
        caption = str(kwargs.get("caption") or "").strip()
        try:
            target = self.workspace_for(event).resolve(path, must_exist=True)
            if target.is_dir():
                return "这是目录，不能当文件发。"
            size = target.stat().st_size
            limit = self._int("send.max_bytes", 52428800)
            if limit > 0 and size > limit:
                return f"文件太大（{human_size(size)}，上限 {human_size(limit)}），没发。"
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import File

            chain = MessageChain()
            chain.chain.append(File(file=str(target)))
            if caption:
                chain.message(caption)
            session_id = str(getattr(event, "unified_msg_origin", "") or "")
            sender = getattr(self.context, "send_message", None)
            if not callable(sender) or not session_id:
                return "拿不到当前会话，没法把文件发出去。"
            await sender(session_id, chain)
            self._note_call("send_file", True, path)
            await self._remember_action(
                event, kind="send_file",
                text=f"我把 {self.workspace_for(event).relpath(target)} 发到了当前会话（{human_size(size)}）。",
                meta={"path": self.workspace_for(event).relpath(target)},
            )
            return f"已把「{self.workspace_for(event).relpath(target)}」（{human_size(size)}）发到这个会话。"
        except WorkspaceError as exc:
            self._note_call("send_file", False, str(exc))
            return f"发文件失败：{exc}"
        except Exception as exc:
            self._note_call("send_file", False, str(exc))
            return f"发文件出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：任意任务（仅 owner）
    @filter.llm_tool(name="toolbox_python")
    async def toolbox_python_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """在工作区里写一段 Python 脚本并运行它——用来完成**任何没有现成工具的活**。

        什么时候用：要处理的活没有对应工具时。例如批量改名/转换、统计与筛选表格、
        生成图表、把多份文档合并、按规则清洗文本、生成 xlsx/pptx、算日期算账……
        不要硬拿 fs_write 手工拼，更不要在没做的情况下说"已经做好了"。

        约定：
        - 当前目录就是工作区，只能读写工作区内的文件；
        - 已安装：pandas、numpy、python-docx、openpyxl、python-pptx、Pillow；
        - **一定要把结果 print 出来**（脚本不打印，等于什么都没做）；
        - 报错就把错误原文贴出来，改一版再跑，别猜。

        Args:
            code(string): 完整可运行的 Python 代码。
            timeout(number): 超时秒数，默认 30，最大 120。
        """
        block = self._gate("python", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "exec")
        if deny:
            return deny
        sim = self._sim_block("toolbox_python", "执行代码")
        if sim:
            return sim
        denied = self._owner_denied(event, tool="python")
        if denied:
            return denied
        code = str(kwargs.get("code") or "")
        try:
            timeout = float(kwargs.get("timeout") or self._float("exec.timeout_seconds", 30.0))
        except (TypeError, ValueError):
            timeout = self._float("exec.timeout_seconds", 30.0)
        timeout = max(1.0, min(120.0, timeout))
        try:
            result = await exec_mod.run_python(
                code,
                cwd=self.workspace_for(event).root,
                timeout=timeout,
                max_output_chars=self._int("exec.max_output_chars", 4000),
                max_code_chars=self._int("exec.max_code_chars", 20000),
            )
        except exec_mod.ExecError as exc:
            self._note_call("python", False, str(exc))
            return f"跑脚本失败：{exc}"
        except Exception as exc:
            self._note_call("python", False, str(exc))
            return f"跑脚本出错：{type(exc).__name__}: {exc}"

        head = exec_mod.first_line(code)
        digest = exec_mod.script_digest(code)
        self._note_call("python", result.ok, f"{digest} {head}"[:200])
        body = result.output or "（脚本没有任何输出——如果它本该产出东西，请让它 print 出来或写文件）"
        state = "成功" if result.ok else f"失败（退出码 {result.code}）"
        return (
            f"脚本{state}，用时 {result.seconds:.1f}s（{digest}｜{head}）\n"
            f"--- 输出 ---\n{body}"
        )

    # ------------------------------------------------------------ 工具：后台任务（仅 owner）
    @filter.llm_tool(name="toolbox_task_start")
    async def toolbox_task_start_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把一段**耗时**的 Python 脚本丢到后台跑，立刻拿到任务号，不占用对话。

        什么时候用：可能超过一两分钟的活（批量处理、爬取、生成大文件、渲染），
        或者你想先起个头、过一会儿再看结果。短活仍用 toolbox_python 更省事。

        结果要 print 出来或写文件，之后用 toolbox_task_status 查。

        Args:
            code(string): 完整可运行的 Python 代码。
            label(string): 这个任务的名字，方便以后认，可省略。
        """
        block = self._gate("task_start", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "task")
        if deny:
            return deny
        denied = self._owner_denied(event)
        if denied:
            return denied
        code = str(kwargs.get("code") or "")
        label = str(kwargs.get("label") or "").strip()
        try:
            record = await self.tasks.start(code, max_code_chars=self._int("exec.max_code_chars", 20000))
        except tasks_mod.TaskError as exc:
            self._note_call("task_start", False, str(exc))
            return f"没起成后台任务：{exc}"
        except Exception as exc:
            self._note_call("task_start", False, str(exc))
            return f"起后台任务时出错：{type(exc).__name__}: {exc}"
        self._note_call("task_start", True, f"{record.task_id} {label}".strip())
        head = f"已在后台开始跑（任务号 {record.task_id}"
        if label:
            head += f"，名字「{label}」"
        head += "）。"
        return (
            f"{head}\n用 toolbox_task_status(task_id=\"{record.task_id}\") 看进度；"
            "跑完了它会给你退出码和输出。"
        )

    @filter.llm_tool(name="toolbox_task_status")
    async def toolbox_task_status_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """查一个后台任务：还在跑吗、跑了多久、退出码是多少、最近的输出是什么。

        Args:
            task_id(string): 任务号（启动时返回的，例如 160233-a1b2）。
        """
        block = self._gate("task_status", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "task")
        if deny:
            return deny
        denied = self._owner_denied(event)
        if denied:
            return denied
        task_id = str(kwargs.get("task_id") or "").strip()
        try:
            await self.tasks.cleanup()
            record = self.tasks.get(task_id)
        except tasks_mod.TaskError as exc:
            self._note_call("task_status", False, str(exc))
            return str(exc)
        except Exception as exc:
            self._note_call("task_status", False, str(exc))
            return f"查任务出错：{type(exc).__name__}: {exc}"
        self._note_call("task_status", True, record.task_id)
        if record.running:
            state = f"还在跑（已 {record.seconds:.1f}s）"
        else:
            state = f"已结束（退出码 {record.returncode if record.returncode is not None else '未知'}）"
        if record.note:
            state += f"；{record.note}"
        body = self.tasks.tail(record)
        return f"任务 {record.task_id}：{state}\n--- 最近输出 ---\n{body}"

    @filter.llm_tool(name="toolbox_task_stop")
    async def toolbox_task_stop_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """停掉一个后台任务（强杀），用于跑歪了或不需要了的情况。

        Args:
            task_id(string): 任务号。
        """
        block = self._gate("task_stop", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "task")
        if deny:
            return deny
        denied = self._owner_denied(event)
        if denied:
            return denied
        task_id = str(kwargs.get("task_id") or "").strip()
        try:
            record = await self.tasks.stop(task_id)
        except tasks_mod.TaskError as exc:
            self._note_call("task_stop", False, str(exc))
            return str(exc)
        except Exception as exc:
            self._note_call("task_stop", False, str(exc))
            return f"停任务出错：{type(exc).__name__}: {exc}"
        self._note_call("task_stop", True, record.task_id)
        if record.running:
            return f"任务 {record.task_id} 好像还在跑，再查一次确认。"
        return f"任务 {record.task_id} 已停止（退出码 {record.returncode if record.returncode is not None else '未知'}）。"

    # ------------------------------------------------------------ 工具：平台动作
    @filter.llm_tool(name="toolbox_poke")
    async def toolbox_poke_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """戳一戳对方（QQ 的"拍一拍"）。群聊会戳当前说话的人，私聊就戳对方。

        什么时候用：想逗一下、提醒一下、或者对方半天没理你时轻轻碰一下。
        不要频繁使用——真人也是偶尔才戳。

        Args:
            target(string): 留空表示当前说话的人；也可以填我自己的 QQ 号。
        """
        block = self._gate("poke", "actions.enabled")
        if block:
            return block
        left = self._action_left("poke")
        if left == 0:
            return f"今天的戳一戳用完了（上限 {self._int('actions.daily_limit', 20)} 次）。"
        target = str(kwargs.get("target") or "").strip()
        try:
            result = await qq_mod.poke(event, target, self_id=self._bot_self_id())
        except qq_mod.QqActionError as exc:
            self._note_call("poke", False, str(exc))
            return f"戳不出去：{exc}"
        except Exception as exc:
            self._note_call("poke", False, str(exc))
            return f"戳的时候出错了：{type(exc).__name__}: {exc}"
        self._note_action("poke")
        self._note_call("poke", True, result)
        return result

    @filter.llm_tool(name="toolbox_like")
    async def toolbox_like_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """给对方的 QQ 名片点赞（每天有次数上限，别刷）。

        Args:
            times(number): 点几个，默认 1，最多按配置上限（默认 10）。
        """
        block = self._gate("like", "actions.enabled")
        if block:
            return block
        left = self._action_left("like")
        if left == 0:
            return f"今天的点赞用完了（上限 {self._int('actions.daily_limit', 20)} 次）。"
        try:
            times = int(kwargs.get("times") or 1)
        except (TypeError, ValueError):
            times = 1
        cap = self._int("actions.like_cap", 10)
        try:
            result = await qq_mod.like(
                event, "", times=times, cap=cap, self_id=self._bot_self_id()
            )
        except qq_mod.QqActionError as exc:
            self._note_call("like", False, str(exc))
            return f"点不了赞：{exc}"
        except Exception as exc:
            self._note_call("like", False, str(exc))
            return f"点赞时出错了：{type(exc).__name__}: {exc}"
        self._note_action("like")
        self._note_call("like", True, result)
        return result

    # ------------------------------------------------------------ 工具：PDF / 图片 / 压缩包
    @filter.llm_tool(name="toolbox_doc_export")
    async def toolbox_doc_export_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把工作区里的文档导出成 PDF（不需要装 LibreOffice）。

        支持：.docx（段落+表格）、.xlsx（表格）、.pptx（每页幻灯片）、.txt/.md 纯文本。
        导出后可以用 toolbox_send_file 发给对方。

        Args:
            path(string): 源文件在工作区里的相对路径。
            out(string): 输出 PDF 的路径，省略则在同目录用同名 .pdf。
            title(string): PDF 标题，省略则用文件名。
        """
        block = self._gate("doc_export", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "office")
        if deny:
            return deny
        path = str(kwargs.get("path") or "").strip()
        title = str(kwargs.get("title") or "").strip()
        try:
            ws = self.workspace_for(event)
            source = ws.resolve(path, must_exist=True)
            suffix = source.suffix.lower()
            out_path = ws.resolve(str(kwargs.get("out") or "").strip() or f"{source.stem}.pdf")
            if out_path.suffix.lower() != ".pdf":
                out_path = out_path.with_suffix(".pdf")

            if suffix == ".docx":
                doc = office_mod.read_docx(source, max_chars=self._int("office.max_chars", 8000) * 4)
                result = pdf_mod.text_to_pdf(doc.text, out_path, title=title or source.stem)
            elif suffix in (".xlsx", ".xlsm"):
                book = office_mod.read_xlsx(source, max_rows=200, max_chars=20000)
                rows = [line.split(" | ") for line in book.text.splitlines() if line and not line.startswith("[工作表")]
                result = pdf_mod.table_to_pdf(rows, out_path, title=title or source.stem)
            elif suffix == ".pptx":
                deck = office_mod.read_pptx(source, max_slides=60, max_chars=20000)
                blocks = [b for b in deck.text.split("\n\n") if b.strip()]
                slides = []
                for block in blocks:
                    lines = [x for x in block.splitlines() if x.strip()]
                    if not lines:
                        continue
                    slides.append((lines[0].replace("[第 ", "第 ").replace(" 页]", ""), lines[1:]))
                result = pdf_mod.slides_to_pdf(slides, out_path, title=title or source.stem)
            elif suffix in (".txt", ".md", ".csv", ".log"):
                text, _ = ws.read_text(path)
                result = pdf_mod.text_to_pdf(text, out_path, title=title or source.stem)
            else:
                return f"这个格式导不了 PDF：{suffix or '无扩展名'}（支持 docx/xlsx/pptx/txt/md/csv）"

            self._note_call("doc_export", True, str(out_path))
            await self._remember_action(
                event, kind="doc_export",
                text=f"我把 {ws.relpath(source)} 导出成了 PDF（{result.pages} 页）：{ws.relpath(out_path)}。",
                meta={"source": ws.relpath(source), "pdf": ws.relpath(out_path)},
            )
            return (
                f"已导出 PDF：「{ws.relpath(out_path)}」（{result.pages} 页，{human_size(result.bytes)}）。\n"
                f"说明：{result.note}"
            )
        except (pdf_mod.PdfError, office_mod.OfficeError, WorkspaceError) as exc:
            self._note_call("doc_export", False, str(exc))
            return f"导出 PDF 失败：{exc}"
        except Exception as exc:
            self._note_call("doc_export", False, str(exc))
            return f"导出 PDF 出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_image")
    async def toolbox_image_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """处理图片：缩放 / 压缩 / 加水印 / 转换格式（不需要装图像软件）。

        Args:
            path(string): 图片在工作区里的相对路径。
            op(string): resize（缩放）/ compress（压缩）/ watermark（水印）/ convert（转换格式）。
            width(number): resize 时的目标宽，0 表示按比例。
            height(number): resize 时的目标高，0 表示按比例。
            quality(number): compress 的质量 10~95，默认 70。
            text(string): watermark 的文字。
            fmt(string): convert 的目标格式（png/jpg/webp/bmp）。
            out(string): 输出路径，省略自动命名。
        """
        block = self._gate("image", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "image")
        if deny:
            return deny
        path = str(kwargs.get("path") or "").strip()
        op = str(kwargs.get("op") or "resize").strip().lower()
        try:
            ws = self.workspace_for(event)
            source = ws.resolve(path, must_exist=True)
            out_raw = str(kwargs.get("out") or "").strip()
            out_path = ws.resolve(out_raw) if out_raw else None
            for key in ("width", "height", "quality"):
                if kwargs.get(key) in ("", None):
                    kwargs[key] = 0
            result = image_mod.run_op(
                source, op,
                width=int(kwargs.get("width") or 0),
                height=int(kwargs.get("height") or 0),
                quality=int(kwargs.get("quality") or 70),
                text=str(kwargs.get("text") or ""),
                fmt=str(kwargs.get("fmt") or "png"),
                out=str(out_path) if out_path else "",
            )
            self._note_call("image", True, f"{op} {result.path.name}")
            saved = result.path if out_path else result.path
            rel = ws.relpath(saved)
            return f"图片已处理（{op}）：「{rel}」{result.width}x{result.height}，{human_size(result.bytes)}。{result.note}"
        except (image_mod.ImageOpError, WorkspaceError) as exc:
            self._note_call("image", False, str(exc))
            return f"图片处理失败：{exc}"
        except Exception as exc:
            self._note_call("image", False, str(exc))
            return f"图片处理出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_archive")
    async def toolbox_archive_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """打包或解包（zip / tar，不需要装压缩软件）。

        Args:
            action(string): pack（打包）或 unpack（解包）。
            paths(string): pack 时用，逗号分隔的工作区相对路径。
            archive(string): 压缩包路径（pack 时是输出、unpack 时是输入）。
            dest(string): unpack 的目标目录，省略则在压缩包同名的文件夹里。
        """
        block = self._gate("archive", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "archive")
        if deny:
            return deny
        sim = self._sim_block("toolbox_archive", "打包/解包")
        if sim:
            return sim
        action = str(kwargs.get("action") or "pack").strip().lower()
        try:
            ws = self.workspace_for(event)
            if action == "pack":
                raw = str(kwargs.get("paths") or "").strip()
                if not raw:
                    return "要打包哪些文件？用逗号分隔给出路径。"
                files = []
                for item in raw.replace("，", ",").split(","):
                    item = item.strip()
                    if not item:
                        continue
                    files.append(ws.resolve(item, must_exist=True))
                out_raw = str(kwargs.get("archive") or "").strip() or "bundle.zip"
                out_path = ws.resolve(out_raw)
                if out_path.suffix.lower() not in (".zip", ".tar", ".gz", ".tgz"):
                    out_path = out_path.with_suffix(".zip")
                result = archive_mod.pack(files, out_path, base=ws.root)
                self._note_call("archive", True, f"pack {result.path.name}")
                return (
                    f"已打包「{ws.relpath(result.path)}」：{result.entries} 个文件，"
                    f"{human_size(result.bytes)}。{result.note}"
                )
            if action == "unpack":
                arc_raw = str(kwargs.get("archive") or "").strip()
                if not arc_raw:
                    return "要解哪个压缩包？给我路径。"
                src = ws.resolve(arc_raw, must_exist=True)
                dest_raw = str(kwargs.get("dest") or "").strip() or src.stem
                dest = ws.resolve(dest_raw)
                result = archive_mod.unpack(src, dest)
                self._note_call("archive", True, f"unpack {src.name}")
                return (
                    f"已解包到「{ws.relpath(dest)}」：{result.entries} 个文件，"
                    f"{human_size(result.bytes)}。{result.note}"
                )
            return f"不认识的动作：{action}（只支持 pack / unpack）"
        except (archive_mod.ArchiveError, WorkspaceError) as exc:
            self._note_call("archive", False, str(exc))
            return f"压缩包操作失败：{exc}"
        except Exception as exc:
            self._note_call("archive", False, str(exc))
            return f"压缩包操作出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：技能库
    @filter.llm_tool(name="toolbox_skill_list")
    async def toolbox_skill_list_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """列出可用的「技能」（一类活的标准做法）。开始一件正经活之前先看一眼有没有对应技能。

        Args:
            keyword(string): 只列出名字或说明里含这个词的技能，可省略。
        """
        block = self._gate("skill_list", "general.enabled")
        if block:
            return block
        try:
            skills = skills_mod.scan(self.skills_root)
        except Exception as exc:
            self._note_call("skill_list", False, str(exc))
            return f"读技能库出错：{type(exc).__name__}: {exc}"
        keyword = str(kwargs.get("keyword") or "").strip().lower()
        if keyword:
            skills = [s for s in skills if keyword in s.name.lower() or keyword in s.description.lower()]
        self._note_call("skill_list", True, keyword or "all")
        if not skills:
            hint = f"（没有匹配「{keyword}」的技能）" if keyword else "（还没有任何技能）"
            return (
                f"{hint}\n技能是 `skills/` 目录下的 .md 文件，由人编写、模型只读。"
                f"目录：{self.skills_root}"
            )
        lines = skills_mod.summary_lines(skills)
        return f"共 {len(skills)} 个技能：\n" + "\n".join(lines) + "\n\n要用哪个就说名字，我用 toolbox_skill 取完整流程。"

    @filter.llm_tool(name="toolbox_skill")
    async def toolbox_skill_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """取一份技能的完整操作流程，然后照着它一步步做。

        Args:
            name(string): 技能名字（先用 toolbox_skill_list 看有哪些）。
        """
        block = self._gate("skill", "general.enabled")
        if block:
            return block
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return "要取哪个技能？先用 toolbox_skill_list 看看有哪些。"
        try:
            skill = skills_mod.find(self.skills_root, name)
        except Exception as exc:
            self._note_call("skill", False, str(exc))
            return f"读技能出错：{type(exc).__name__}: {exc}"
        if skill is None:
            others = [s.name for s in skills_mod.scan(self.skills_root)][:8]
            tail = f"；现有：{'、'.join(others)}" if others else ""
            self._note_call("skill", False, f"未找到 {name}")
            return f"没有这个技能：{name}{tail}"
        self._note_call("skill", True, skill.name)
        return (
            f"技能「{skill.name}」的操作流程如下，照着做，需要时再用工具：\n"
            f"-----\n{skill.body}\n-----"
        )

    # ------------------------------------------------------------ 工具：并行执行
    @filter.llm_tool(name="toolbox_parallel")
    async def toolbox_parallel_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """**并行**跑几段互不依赖的脚本，一起等结果（比一个个跑快）。

        什么时候用：几件事彼此独立、都挺耗时。比如同时抓三个网站、同时处理三批文件、
        同时算三种统计。**有先后依赖的活不要用这个**，那种要串行。

        Args:
            tasks(string): JSON 数组，每项含 name 与 code。例如：
                [{"name":"抓A","code":"print('A')"},{"name":"抓B","code":"print('B')"}]
            timeout(number): 单路超时秒数，默认 60，最大 300。
        """
        block = self._gate("parallel", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "exec")
        if deny:
            return deny
        raw = kwargs.get("tasks")
        try:
            tasks = parallel_mod.parse_tasks(raw if raw is not None else "")
        except parallel_mod.ParallelError as exc:
            self._note_call("parallel", False, str(exc))
            return f"任务格式有问题：{exc}"
        try:
            timeout = float(kwargs.get("timeout") or 60.0)
        except (TypeError, ValueError):
            timeout = 60.0
        timeout = max(5.0, min(300.0, timeout))
        ws = self.workspace_for(event)
        try:
            results = await parallel_mod.run_many(
                tasks,
                cwd=ws.root,
                timeout=timeout,
                max_output_chars=self._int("exec.max_output_chars", 2000),
                max_concurrent=self._int("parallel.max_concurrent", 3),
            )
        except parallel_mod.ParallelError as exc:
            self._note_call("parallel", False, str(exc))
            return f"并行执行失败：{exc}"
        except Exception as exc:
            self._note_call("parallel", False, str(exc))
            return f"并行执行出错：{type(exc).__name__}: {exc}"
        ok_count = sum(1 for r in results if r.ok)
        self._note_call("parallel", ok_count == len(results), f"{ok_count}/{len(results)}")
        return parallel_mod.summarize(results)

    @filter.llm_tool(name="toolbox_task_wait")
    async def toolbox_task_wait_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """等一个后台任务跑完（或等到超时），省得反复查。

        Args:
            task_id(string): 任务号。
            timeout(number): 最多等多少秒，默认 120，最大 600。
        """
        block = self._gate("task_wait", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "task")
        if deny:
            return deny
        task_id = str(kwargs.get("task_id") or "").strip()
        try:
            limit = float(kwargs.get("timeout") or 120.0)
        except (TypeError, ValueError):
            limit = 120.0
        limit = max(5.0, min(600.0, limit))
        try:
            await self.tasks.cleanup()
            record = self.tasks.get(task_id)
        except tasks_mod.TaskError as exc:
            self._note_call("task_wait", False, str(exc))
            return str(exc)

        waited = 0.0
        step = 1.0
        while record.running and waited < limit:
            await asyncio.sleep(step)
            waited += step
            step = min(3.0, step + 0.5)
        state = "已结束" if not record.running else f"还在跑（等了 {waited:.0f}s，已到上限）"
        self._note_call("task_wait", True, f"{task_id} {state}")
        body = self.tasks.tail(record)
        code = record.returncode if record.returncode is not None else "未知"
        return f"任务 {task_id}：{state}（退出码 {code}）\n--- 最近输出 ---\n{body}"

    # ------------------------------------------------------------ 工具：精确编辑 / 查找 / 目标 / 子代理
    @filter.llm_tool(name="toolbox_edit")
    async def toolbox_edit_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """**精确编辑**文件：把文件里的一小段原文换成新内容（不用重写整份）。

        比 fs_write 安全：只动你指定的那一段，其它内容原样保留。
        要替换的原文必须**唯一存在**，否则会报错让你写得更具体。

        Args:
            path(string): 文件在工作区里的相对路径。
            old_text(string): 要被替换的原文（要足够独特，只匹配一处）。
            new_text(string): 替换成什么。
            replace_all(boolean): 是否替换所有匹配（默认 false）。
        """
        block = self._gate("edit", "fs.enabled")
        if block:
            return block
        deny = self._allow(event, "write")
        if deny:
            return deny
        sim = self._sim_block("toolbox_edit", "编辑文件")
        if sim:
            return sim
        path = str(kwargs.get("path") or "").strip()
        old_text = str(kwargs.get("old_text") or "")
        new_text = str(kwargs.get("new_text") or "")
        replace_all = kwargs.get("replace_all") is True
        try:
            ws = self.workspace_for(event)
            target = ws.resolve(path, must_exist=True)
            result = fstools_mod.edit_text(
                target, old_text=old_text, new_text=new_text, replace_all=replace_all
            )
            self._note_call("edit", True, path)
            return (
                f"已修改「{ws.relpath(target)}」：替换 {result.replaced} 处，"
                f"{result.before_bytes} → {result.after_bytes} 字节。\n片段：{result.preview}"
            )
        except (fstools_mod.FsToolError, WorkspaceError) as exc:
            self._note_call("edit", False, str(exc))
            return f"编辑失败：{exc}"
        except Exception as exc:
            self._note_call("edit", False, str(exc))
            return f"编辑出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_glob")
    async def toolbox_glob_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """按通配符查找文件（支持 `**` 递归），按修改时间倒序返回。

        找文件用这个，比一层层 fs_list 快。

        Args:
            pattern(string): 模式，例如 `*.md`、`notes/**/*.txt`、`**/*.py`。
            limit(number): 最多返回多少条，默认 30。
        """
        block = self._gate("glob", "fs.enabled")
        if block:
            return block
        pattern = str(kwargs.get("pattern") or "").strip()
        try:
            limit = int(kwargs.get("limit") or 30)
        except (TypeError, ValueError):
            limit = 30
        try:
            ws = self.workspace_for(event)
            hits = fstools_mod.glob_files(ws.root, pattern, limit=max(1, min(200, limit)))
            self._note_call("glob", True, pattern)
            if not hits:
                return f"没有匹配「{pattern}」的文件。"
            lines = [f"匹配「{pattern}」共 {len(hits)} 个（按修改时间倒序）："]
            for hit in hits:
                size = "" if hit.is_dir else f"  {human_size(hit.size)}"
                lines.append(f"  {hit.path}{'/' if hit.is_dir else ''}{size}  {fstools_mod.human_mtime(hit.mtime)}")
            return "\n".join(lines)
        except (fstools_mod.FsToolError, WorkspaceError) as exc:
            self._note_call("glob", False, str(exc))
            return f"查找失败：{exc}"
        except Exception as exc:
            self._note_call("glob", False, str(exc))
            return f"查找出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_goal")
    async def toolbox_goal_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """长期目标：跨会话记住「要做成什么事」（不是"说过要做"，是"要达成的结果"）。

        Args:
            action(string): list（列出）/ add（新建）/ update（改备注）/ done（完成）/ drop（放弃）。
            goal_id(string): 目标编号（update/done/drop 时要）。
            title(string): add 时的目标描述。
            note(string): 备注 / 进度。
        """
        block = self._gate("goal", "general.enabled")
        if block:
            return block
        action = str(kwargs.get("action") or "list").strip().lower()
        goal_id = str(kwargs.get("goal_id") or "").strip()
        title = str(kwargs.get("title") or "").strip()
        note = str(kwargs.get("note") or "").strip()
        try:
            if action == "list":
                include_closed = kwargs.get("note") == "all" or str(kwargs.get("title") or "") == "all"
                items = self.goals.list(include_closed=include_closed)
                self._note_call("goal", True, "list")
                if not items:
                    return "现在没有进行中的目标。"
                return "目标：\n" + "\n".join(g.line() for g in items)
            if action == "add":
                goal = self.goals.add(title, note)
                self._note_call("goal", True, f"add {goal.id}")
                return f"已立目标 [{goal.id}]：{goal.title}"
            if action == "update":
                goal = self.goals.update(goal_id, note=note)
                self._note_call("goal", True, f"update {goal.id}")
                return f"目标 [{goal.id}] 已更新：{goal.title}（备注：{goal.note or '无'}）"
            if action in ("done", "drop"):
                state = "done" if action == "done" else "dropped"
                goal = self.goals.close(goal_id, state=state, note=note)
                self._note_call("goal", True, f"{action} {goal.id}")
                return f"目标 [{goal.id}] 已{'完成' if state == 'done' else '放弃'}：{goal.title}"
            return f"不认识的动作：{action}（支持 list/add/update/done/drop）"
        except goals_mod.GoalError as exc:
            self._note_call("goal", False, str(exc))
            return f"目标操作失败：{exc}"
        except Exception as exc:
            self._note_call("goal", False, str(exc))
            return f"目标操作出错：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="toolbox_delegate_list")
    async def toolbox_delegate_list_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """列出可用的「专家」（子代理），每个一句说明。要用子代理前先看一眼。"""
        block = self._gate("delegate_list", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "exec")
        if deny:
            return deny
        experts = self._experts()
        budget = self.delegate_budget
        head = f"今日子代理额度：{budget.left()}/{budget.daily_limit} 次"
        if not experts:
            return (
                f"{head}\n还没有配置专家（配置项 delegate.agents 是 JSON 数组）。"
                "不配也能用 `toolbox_delegate(task=...)`，那就是「默认」通用推理。"
            )
        lines = [head, f"共 {len(experts)} 个专家："]
        lines.extend(e.one_line() for e in experts)
        return "\n".join(lines)

    @filter.llm_tool(name="toolbox_delegate")
    async def toolbox_delegate_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """把一件**独立的小事**交给子代理（专家）处理：它在干净上下文里想，不看当前对话。

        分工原则：**你负责取数，它负责思考**。材料（文件内容、要点）你要自己先用工具读出来，
        放进 material 交给它；它没有工具，拿不到别的东西。

        适合：写作、审稿、翻译、长文摘要、方案对比这类「想清楚再给结论」的活。
        不适合：需要当前对话上下文、需要动手改文件的事。

        Args:
            task(string): 要它做什么（简短明确）。
            agent(string): 用哪个专家（先看 toolbox_delegate_list），省略则用默认。
            material(string): 相关材料，可省略（上限 4000 字）。
        """
        block = self._gate("delegate", "exec.enabled")
        if block:
            return block
        deny = self._allow(event, "exec")
        if deny:
            return deny
        task = str(kwargs.get("task") or "").strip()
        material = str(kwargs.get("material") or "")
        agent_name = str(kwargs.get("agent") or "").strip()

        budget = self.delegate_budget
        if budget.left() <= 0:
            self._note_call("delegate", False, "daily_limit")
            return f"今天的子代理额度已用完（{budget.daily_limit} 次），明天再交，或者自己直接做。"

        expert = None
        if agent_name:
            expert = delegate_mod.find_expert(self._experts(), agent_name)
            if expert is None:
                names = "、".join(e.name for e in self._experts()) or "（未配置）"
                self._note_call("delegate", False, f"未知专家 {agent_name}")
                return f"没有这个专家：{agent_name}。现有：{names}"

        try:
            provider = self.context.get_using_provider()
        except Exception:
            provider = None
        # per-agent provider 覆盖（便宜活可以用便宜模型）
        if expert is not None and expert.provider_id:
            try:
                override = self.context.get_provider_by_id(expert.provider_id)
                if override is not None:
                    provider = override
            except Exception:
                pass
        if provider is None:
            self._note_call("delegate", False, "无可用 provider")
            return "现在没有可用的模型，交不出去。"

        if not budget.take():
            self._note_call("delegate", False, "daily_limit")
            return "今天的子代理额度已用完。"
        try:
            result = await delegate_mod.delegate(provider, task=task, material=material, expert=expert)
            self._note_call("delegate", True, (expert.name if expert else "默认") + " / " + task[:30])
            return f"{result.header}\n{result.text}"
        except delegate_mod.DelegateError as exc:
            self._note_call("delegate", False, str(exc))
            return f"子代理失败：{exc}"
        except Exception as exc:
            self._note_call("delegate", False, str(exc))
            return f"子代理出错：{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 工具：命令（默认关）
    @filter.llm_tool(name="toolbox_shell_run")
    async def toolbox_shell_run_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """在工作区里执行一条白名单内的命令（默认关闭；只适合 git 之类的只读查询）。

        Args:
            command(string): 完整命令，例如 "git status"。
        """
        block = self._gate("shell_run", "shell.enabled")
        if block:
            return block
        deny = self._allow(event, "shell")
        if deny:
            return deny
        sim = self._sim_block("toolbox_shell_run", "执行命令")
        if sim:
            return sim
        command = str(kwargs.get("command") or "").strip()
        try:
            allow = shell_mod.parse_allowlist(self._str("shell.allowlist", ""))
            code, output = await shell_mod.run_command(
                command,
                allowlist=allow,
                cwd=self.workspace_for(event).root,
                timeout=self._float("shell.timeout_seconds", 15.0),
                max_output_chars=self._int("shell.max_output_chars", 4000),
            )
            self._note_call("shell_run", code == 0, command)
            body = output or "（没有输出）"
            return f"命令：{command}\n退出码：{code}\n输出：\n{body}"
        except shell_mod.ShellError as exc:
            self._note_call("shell_run", False, str(exc))
            return f"执行失败：{exc}"
        except Exception as exc:
            self._note_call("shell_run", False, str(exc))
            return f"执行出错：{type(exc).__name__}: {exc}"
