"""工作区与文件工具：所有文件操作都被关在一个目录里。

设计要点（安全优先）：
- **路径监狱**：任何相对路径都经 `resolve()` 后必须仍在工作区根目录内，`..`、绝对路径、
  符号链接逃逸一律拒绝；这是本插件最重要的不变量。
- **尺寸上限**：读、写、下载都按配置截断/拒绝，避免把大文件塞进上下文。
- **敏感名拒绝**：即便在工作区内，也不碰 `.env` / `*.key` / `*.pem` / `id_rsa` 之类。
- 纯标准库实现，便于单测（不依赖 AstrBot 运行时）。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class WorkspaceError(Exception):
    """工作区操作失败（信息面向模型可读）。"""


# 即便在工作区内也拒绝读写的文件名模式（防止误把密钥放进来后被读走）
_SENSITIVE_PATTERNS = (
    re.compile(r"^\.env(\.|$)", re.I),
    re.compile(r"\.(pem|key|pfx|p12)$", re.I),
    re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)$", re.I),
    re.compile(r"(^|[.\-_])secret(s)?([.\-_]|$)", re.I),
    re.compile(r"(^|[.\-_])credential(s)?([.\-_]|$)", re.I),
)

_TEXT_READ_SUFFIX_HINT = (
    ".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".tsv", ".log", ".py", ".js",
    ".ts", ".html", ".htm", ".css", ".xml", ".ini", ".toml", ".sh", ".bat", ".ps1",
)


@dataclass(slots=True)
class FileInfo:
    path: str
    size: int
    is_dir: bool
    mtime: float


class Workspace:
    """一个受限制的工作区目录。"""

    def __init__(
        self,
        root: Path,
        *,
        max_read_bytes: int = 256 * 1024,
        max_write_bytes: int = 1024 * 1024,
        max_download_bytes: int = 50 * 1024 * 1024,
        max_list_entries: int = 200,
        allow_write: bool = True,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_read_bytes = max(1024, int(max_read_bytes))
        self.max_write_bytes = max(1024, int(max_write_bytes))
        self.max_download_bytes = max(1024, int(max_download_bytes))
        self.max_list_entries = max(1, int(max_list_entries))
        self.allow_write = bool(allow_write)

    # ------------------------------------------------------------------ 路径监狱
    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        """把用户给的相对路径解析成工作区内的绝对路径；越界即拒绝。"""
        raw = str(relative or "").strip().replace("\\", "/")
        if raw in ("", "."):
            candidate = self.root
        else:
            if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
                raise WorkspaceError("只接受工作区内的相对路径（不要用绝对路径）")
            if any(part == ".." for part in raw.split("/")):
                raise WorkspaceError("路径里不允许出现 `..`")
            candidate = self.root / raw
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise WorkspaceError(f"路径无法解析：{exc}") from exc
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise WorkspaceError("路径越出工作区范围，已拒绝") from exc
        if must_exist and not resolved.exists():
            raise WorkspaceError(f"不存在：{self.relpath(resolved)}")
        return resolved

    def relpath(self, absolute: Path) -> str:
        try:
            return absolute.resolve().relative_to(self.root.resolve()).as_posix() or "."
        except ValueError:
            return "?"

    def _check_sensitive(self, path: Path) -> None:
        name = path.name
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(name):
                raise WorkspaceError(f"拒绝访问疑似敏感文件：{name}")

    # ------------------------------------------------------------------ 读
    def list_dir(self, relative: str = "") -> tuple[str, list[FileInfo]]:
        target = self.resolve(relative, must_exist=True)
        if not target.is_dir():
            raise WorkspaceError("这不是目录")
        items: list[FileInfo] = []
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if len(items) >= self.max_list_entries:
                break
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append(
                FileInfo(
                    path=self.relpath(entry),
                    size=int(stat.st_size),
                    is_dir=entry.is_dir(),
                    mtime=float(stat.st_mtime),
                )
            )
        return self.relpath(target), items

    def read_text(self, relative: str, *, head: int = 0, tail: int = 0) -> tuple[str, int]:
        """读取工作区内的文本文件，返回 (内容, 原始字节数)。"""
        target = self.resolve(relative, must_exist=True)
        self._check_sensitive(target)
        if target.is_dir():
            raise WorkspaceError("这是目录，不能当文件读；用 fs_list 看目录内容")
        size = target.stat().st_size
        with target.open("rb") as fh:
            if tail and not head:
                # 取尾部：从后往前读一个窗口
                want = min(size, self.max_read_bytes)
                fh.seek(max(0, size - want))
                raw = fh.read(want)
            else:
                raw = fh.read(self.max_read_bytes)
        text = self._decode(raw, target)
        lines = text.splitlines()
        if head and head > 0:
            text = "\n".join(lines[: int(head)])
        elif tail and tail > 0:
            text = "\n".join(lines[-int(tail):])
        else:
            text = "\n".join(lines)
        if size > self.max_read_bytes:
            text += f"\n…（文件共 {size} 字节，这里只读了前 {self.max_read_bytes} 字节）"
        return text, size

    @staticmethod
    def _decode(raw: bytes, target: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ 写
    def write_text(self, relative: str, content: str, *, overwrite: bool = True) -> str:
        if not self.allow_write:
            raise WorkspaceError("工作区当前是只读模式（可在配置里开启写入）")
        target = self.resolve(relative)
        if target == self.root or target.is_dir():
            raise WorkspaceError("目标必须是文件路径")
        self._check_sensitive(target)
        data = str(content or "").encode("utf-8")
        if len(data) > self.max_write_bytes:
            raise WorkspaceError(f"内容太大（{len(data)} 字节，上限 {self.max_write_bytes}）")
        if target.exists() and not overwrite:
            raise WorkspaceError("文件已存在且未允许覆盖")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return self.relpath(target)

    def mkdir(self, relative: str) -> str:
        if not self.allow_write:
            raise WorkspaceError("工作区当前是只读模式")
        target = self.resolve(relative)
        target.mkdir(parents=True, exist_ok=True)
        return self.relpath(target)

    def save_bytes(self, relative: str, data: bytes) -> str:
        """下载落盘用（同一套监狱与上限）。"""
        if not self.allow_write:
            raise WorkspaceError("工作区当前是只读模式")
        target = self.resolve(relative)
        if target == self.root or target.is_dir():
            raise WorkspaceError("目标必须是文件路径")
        self._check_sensitive(target)
        if len(data) > self.max_download_bytes:
            raise WorkspaceError(f"文件太大（{len(data)} 字节，上限 {self.max_download_bytes}）")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return self.relpath(target)

    def delete(self, relative: str) -> str:
        if not self.allow_write:
            raise WorkspaceError("工作区当前是只读模式")
        target = self.resolve(relative, must_exist=True)
        if target == self.root:
            raise WorkspaceError("不能删除工作区根目录")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return self.relpath(target)

    # ------------------------------------------------------------------ 搜索
    def search_text(
        self,
        pattern: str,
        *,
        relative: str = "",
        glob: str = "*",
        max_hits: int = 30,
        max_file_bytes: int = 512 * 1024,
    ) -> tuple[list[tuple[str, int, str]], int]:
        """在工作区内按正则搜索文本文件，返回 ([(相对路径, 行号, 行内容)], 扫描文件数)。"""
        try:
            regex = re.compile(pattern, re.I)
        except re.error as exc:
            raise WorkspaceError(f"正则表达式无效：{exc}") from exc
        base = self.resolve(relative, must_exist=True)
        if base.is_file():
            candidates: Iterable[Path] = [base]
        else:
            candidates = base.rglob(glob or "*")
        hits: list[tuple[str, int, str]] = []
        scanned = 0
        for path in candidates:
            if len(hits) >= max_hits:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() and path.suffix.lower() not in _TEXT_READ_SUFFIX_HINT:
                # 只搜看起来是文本的文件（体积也更可控）
                try:
                    if path.stat().st_size > max_file_bytes:
                        continue
                except OSError:
                    continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            scanned += 1
            for index, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append((self.relpath(path), index, line.strip()[:200]))
                    if len(hits) >= max_hits:
                        break
        return hits, scanned

    # ------------------------------------------------------------------ 概览
    def summary(self) -> dict[str, object]:
        files = 0
        dirs = 0
        total = 0
        for entry in self.root.rglob("*"):
            try:
                if entry.is_dir():
                    dirs += 1
                else:
                    files += 1
                    total += entry.stat().st_size
            except OSError:
                continue
        return {
            "root": str(self.root),
            "files": files,
            "dirs": dirs,
            "bytes": total,
            "free": self._free_bytes(),
        }

    def _free_bytes(self) -> int:
        try:
            return int(shutil.disk_usage(str(self.root)).free)
        except OSError:
            return -1


def human_size(size: int) -> str:
    """把字节数变成人类可读的粗略单位。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def safe_filename_from_url(url: str, fallback: str = "download.bin") -> str:
    """从 URL 猜一个安全的文件名（不含路径分隔符与怪异字符）。"""
    try:
        name = os.path.basename(str(url).split("?")[0].split("#")[0])
    except Exception:
        name = ""
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name or "").strip("._")
    return name[:120] or fallback
