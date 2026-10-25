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

import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from . import PLUGIN_DISPLAY, PLUGIN_NAME, PLUGIN_VERSION
from . import shell as shell_mod
from . import web as web_mod
from .workspace import Workspace, WorkspaceError, human_size, safe_filename_from_url

_TOOL_TITLES = {
    "info": "工作区概况",
    "fs_list": "列目录",
    "fs_read": "读文件",
    "fs_write": "写文件",
    "fs_search": "搜内容",
    "web_fetch": "抓网页",
    "web_search": "联网搜索",
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
        self._usage: dict[str, int] = {}
        self._audit: list[dict[str, Any]] = []
        logger.info("[Toolbox] %s %s 已加载，工作区=%s", PLUGIN_DISPLAY, PLUGIN_VERSION, self.workspace_root)

    # ------------------------------------------------------------ 生命周期
    async def initialize(self) -> None:
        try:
            (self.workspace_root / "README.txt").write_text(
                "这是「为你动手的工具箱」的工作区。\n"
                "模型只能在这个目录里读写文件、下载文件；外面的东西它碰不到。\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[Toolbox] 初始化工作区说明失败: %s", exc)

    async def terminate(self) -> None:
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

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _quota_left(self) -> int:
        limit = self._int("limits.daily_calls", 200)
        if limit <= 0:
            return -1
        used = self._usage.get(f"{self._today()}:total", 0)
        return max(0, limit - used)

    def _note_call(self, tool: str, ok: bool, detail: str = "") -> None:
        day = self._today()
        self._usage[f"{day}:total"] = self._usage.get(f"{day}:total", 0) + 1
        self._usage[f"{day}:{tool}"] = self._usage.get(f"{day}:{tool}", 0) + 1
        self._audit.append(
            {
                "time": time.strftime("%H:%M:%S", time.localtime()),
                "tool": tool,
                "ok": bool(ok),
                "detail": str(detail or "")[:200],
            }
        )
        del self._audit[:-60]

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
    @filter.llm_tool(name="toolbox_info")
    async def toolbox_info_tool(self, event: AstrMessageEvent, **kwargs: Any) -> str:
        """查看工作区现状与可用工具（文件放哪、还有多少空间、哪些工具开着）。

        在你准备读写文件、下载东西之前先看一眼，避免瞎猜路径。
        """
        block = self._gate("info", "general.enabled")
        if block:
            return block
        try:
            data = self.workspace.summary()
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
                f"今日已动手：{self._usage.get(f'{self._today()}:total', 0)} 次"
                + (f"（上限 {self._int('limits.daily_calls', 200)}）" if self._int("limits.daily_calls", 200) > 0 else ""),
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
            rel, items = self.workspace.list_dir(path)
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
            text, size = self.workspace.read_text(path, head=head, tail=tail)
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
        path = str(kwargs.get("path") or "").strip()
        content = str(kwargs.get("content") or "")
        overwrite_raw = kwargs.get("overwrite", True)
        overwrite = overwrite_raw if isinstance(overwrite_raw, bool) else str(overwrite_raw).lower() not in ("0", "false", "否")
        try:
            saved = self.workspace.write_text(path, content, overwrite=overwrite)
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
            hits, scanned = self.workspace.search_text(
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
            saved = self.workspace.save_bytes(target, data)
            self._note_call("download", True, saved)
            extra = f"（{ctype}）" if ctype else ""
            return f"已下载到「{saved}」，{human_size(len(data))}{extra}。"
        except (web_mod.WebError, WorkspaceError) as exc:
            self._note_call("download", False, str(exc))
            return f"下载失败：{exc}"
        except Exception as exc:
            self._note_call("download", False, str(exc))
            return f"下载出错：{type(exc).__name__}: {exc}"

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
        command = str(kwargs.get("command") or "").strip()
        try:
            allow = shell_mod.parse_allowlist(self._str("shell.allowlist", "git"))
            code, output = await shell_mod.run_command(
                command,
                allowlist=allow,
                cwd=self.workspace_root,
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
