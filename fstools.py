"""文件精确编辑与模式查找（对应 DSH 的 edit / glob）。

为什么需要"精确编辑"：以前只能整份覆盖写，改一行也要重写全文 ——
既容易把别的内容改掉，也费 token。精确编辑要求"要替换的原文必须存在且唯一"，
找不到或有多处就报错，逼调用方把目标说清楚。

为什么需要"模式查找"：列目录只能看一层，真实找文件常常是"所有 md 里哪个提到 X"。
`glob` 支持 `**` 递归与通配符，按修改时间倒序返回，最常用的排前面。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class FsToolError(Exception):
    """文件操作失败（信息面向模型可读）。"""


@dataclass(slots=True)
class EditResult:
    path: str
    replaced: int
    before_bytes: int
    after_bytes: int
    preview: str = ""


@dataclass(slots=True)
class GlobHit:
    path: str
    size: int
    mtime: float
    is_dir: bool


def edit_text(
    path: Path,
    *,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> EditResult:
    """把文件里的 old_text 换成 new_text。

    规则（故意严格）：
    - old_text 必须在文件里**恰好出现一次**；出现 0 次或多处都报错，除非显式 `replace_all`；
    - old_text 为空 → 拒绝（那会变成无意义的全局插入）；
    - 文件过大（超 max_file_bytes）→ 拒绝，避免把大文件读进内存。
    """
    target = Path(path)
    if not target.is_file():
        raise FsToolError(f"文件不存在：{target.name}")
    if not str(old_text or "").strip():
        raise FsToolError("要替换的原文是空的，拒绝执行")
    size = target.stat().st_size
    if size > max_file_bytes:
        raise FsToolError(f"文件太大（{size} 字节，上限 {max_file_bytes}），请改用其他方式")

    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FsToolError(f"读文件失败：{exc}") from exc

    count = raw.count(old_text)
    if count == 0:
        raise FsToolError("文件里找不到这段原文（可能已被改过或换行不一致）")
    if count > 1 and not replace_all:
        raise FsToolError(f"这段原文出现了 {count} 次，不唯一；要么写更长的上下文，要么加 replace_all")
    if count > 200:
        raise FsToolError(f"这段原文出现 {count} 次，太多，拒绝批量替换")

    updated = raw.replace(old_text, new_text) if replace_all else raw.replace(old_text, new_text, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise FsToolError(f"写文件失败：{exc}") from exc

    index = updated.find(str(new_text)[:40]) if new_text else 0
    preview = updated[max(0, index - 40): index + 120].replace("\n", "⏎") if index >= 0 else ""
    return EditResult(
        path=target.name,
        replaced=count if replace_all else 1,
        before_bytes=size,
        after_bytes=len(updated.encode("utf-8")),
        preview=preview,
    )


def glob_files(
    root: Path,
    pattern: str,
    *,
    limit: int = 100,
    include_dirs: bool = False,
    max_scan: int = 20000,
) -> list[GlobHit]:
    """按通配符找文件（支持 `**` 递归），按修改时间倒序返回。"""
    base = Path(root)
    if not base.is_dir():
        raise FsToolError("工作区目录不存在")
    text = str(pattern or "").strip()
    if not text:
        raise FsToolError("要给一个模式，例如 `*.md` 或 `**/*.py`")
    if text.startswith("/") or ":" in text[:3]:
        raise FsToolError("模式要用工作区内的相对写法（不要绝对路径）")
    if ".." in text.split("/"):
        raise FsToolError("模式里不允许出现 `..`")

    hits: list[GlobHit] = []
    scanned = 0
    try:
        iterator = base.glob(text)
        for path in iterator:
            scanned += 1
            if scanned > max_scan:
                break
            try:
                if path.is_dir() and not include_dirs:
                    continue
                stat = path.stat()
            except OSError:
                continue
            try:
                rel = path.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                continue
            hits.append(GlobHit(path=rel, size=int(stat.st_size), mtime=float(stat.st_mtime),
                                is_dir=path.is_dir()))
    except (ValueError, OSError) as exc:
        raise FsToolError(f"模式无效：{exc}") from exc

    hits.sort(key=lambda item: item.mtime, reverse=True)
    return hits[: max(1, min(500, int(limit)))]


def human_mtime(timestamp: float) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except (ValueError, OSError):
        return "?"
