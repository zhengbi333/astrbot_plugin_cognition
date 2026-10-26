"""「经验条目」的纯函数层：命名、前页、正文裁剪。

不依赖 AstrBot 运行时，便于单测。设计目标是让 `toolbox_learn` 落盘的东西
**人可读、日后可检索**：文件名带日期与来源，正文带 front-matter。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

_SLUG_KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_FRONT_MATTER_MAX_TITLE = 80


def slugify(text: str, *, fallback: str = "note", max_len: int = 40) -> str:
    """把标题/URL 变成安全文件名片段（保留中英文与数字）。"""
    raw = _SLUG_KEEP.sub("-", str(text or "")).strip("-")
    raw = re.sub(r"-{2,}", "-", raw)
    if not raw:
        raw = fallback
    return raw[:max_len].strip("-") or fallback


def note_filename(*, title: str = "", url: str = "", when: float | None = None, ext: str = "md") -> str:
    """生成 `notes/<日期>-<slug>.md` 风格的文件名（不含 notes/ 前缀）。"""
    stamp = time.strftime("%Y-%m-%d", time.localtime(when if when is not None else time.time()))
    base = ""
    if title:
        base = slugify(title, fallback="")
    if not base and url:
        # 从 URL 里取最后一段有意义的路径
        tail = str(url).split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
        base = slugify(tail, fallback="", max_len=30)
    if not base:
        base = "note"
    # 同一天多次抓同一页时，靠秒级后缀区分，避免互相覆盖
    hhmm = time.strftime("%H%M", time.localtime(when if when is not None else time.time()))
    return f"{stamp}-{base}-{hhmm}.{ext.lstrip('.') or 'md'}"


def one_line_title(text: str, *, limit: int = _FRONT_MATTER_MAX_TITLE) -> str:
    """从正文头部猜一个标题（取第一行非空文本），压成单行。"""
    for line in str(text or "").splitlines():
        line = line.strip().strip("#").strip()
        if line:
            return line[:limit]
    return ""


def compose_note(
    *,
    title: str,
    url: str,
    body: str,
    query: str = "",
    when: float | None = None,
    max_body_chars: int = 6000,
) -> str:
    """拼出带 front-matter 的 Markdown 条目。"""
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when if when is not None else time.time()))
    body_text = str(body or "").strip()
    if len(body_text) > max_body_chars:
        body_text = body_text[:max_body_chars].rstrip() + f"\n\n…（正文过长，已截断到 {max_body_chars} 字）"
    lines = [
        "---",
        f"title: {one_line_title(title) or '（无标题）'}",
        f"fetched_at: {stamp}",
        f"source_url: {url or '（无）'}",
    ]
    if query:
        lines.append(f"search_query: {query}")
    lines += ["---", "", f"# {one_line_title(title) or '（无标题）'}", "", body_text, ""]
    return "\n".join(lines)


def summarize_for_reply(note: str, *, limit: int = 600) -> tuple[str, bool]:
    """给模型的简短回执：条目要点 + 是否被截断。"""
    text = re.sub(r"^---.*?---\s*", "", str(note or ""), flags=re.S).strip()
    text = re.sub(r"\s+", " ", text)
    truncated = len(text) > limit
    return (text[:limit] + ("…" if truncated else "")), truncated


@dataclass(slots=True)
class NoteResult:
    path: str
    chars: int
    excerpt: str
    truncated: bool
