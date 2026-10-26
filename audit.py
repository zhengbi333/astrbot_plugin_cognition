"""调用审计与配额：纯 Python，可单测。

为什么要独立成模块：审计/配额是"信任"的一部分——使用者需要能查到
Bot 到底动过什么、今天还剩多少额度。逻辑从 main.py 抽出来才好测。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AuditEntry:
    time: str
    tool: str
    ok: bool
    detail: str = ""

    def as_line(self) -> str:
        mark = "✓" if self.ok else "✗"
        tail = f" — {self.detail}" if self.detail else ""
        return f"{self.time} {mark} {self.tool}{tail}"

    def to_dict(self) -> dict:
        return {"time": self.time, "tool": self.tool, "ok": self.ok, "detail": self.detail}


class AuditLog:
    """定长环形日志：只保留最近 N 条，内存占用可预期。"""

    def __init__(
        self,
        capacity: int = 60,
        path: Path | str | None = None,
        max_file_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.capacity = max(1, int(capacity))
        self._items: list[AuditEntry] = []
        self.path = Path(path) if path else None
        self.max_file_bytes = max(64 * 1024, int(max_file_bytes))

    def note(self, tool: str, ok: bool, detail: str = "", *, when: float | None = None) -> AuditEntry:
        entry = AuditEntry(
            time=time.strftime("%H:%M:%S", time.localtime(when if when is not None else time.time())),
            tool=str(tool or "?"),
            ok=bool(ok),
            detail=str(detail or "")[:200],
        )
        self._items.append(entry)
        if len(self._items) > self.capacity:
            del self._items[: len(self._items) - self.capacity]
        self._append_file(entry)
        return entry

    # ------------------------------------------------------------ 落盘
    def _append_file(self, entry: AuditEntry) -> None:
        """把一条记录追加到 jsonl（失败不影响主流程）。"""
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_file_bytes:
                self._rotate()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _rotate(self) -> None:
        """文件过大时只保留后半部分（避免无限增长）。"""
        try:
            data = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            keep = data[len(data) // 2:]
            self.path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        except OSError:
            pass

    def load(self) -> int:
        """从 jsonl 载入最近若干条到内存（用于重启后仍能查询）。返回载入条数。"""
        if self.path is None or not self.path.exists():
            return 0
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return 0
        loaded = 0
        for line in lines[-self.capacity:]:
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                continue
            self._items.append(
                AuditEntry(
                    time=str(data.get("time") or ""),
                    tool=str(data.get("tool") or "?"),
                    ok=bool(data.get("ok")),
                    detail=str(data.get("detail") or "")[:200],
                )
            )
            loaded += 1
        if len(self._items) > self.capacity:
            del self._items[: len(self._items) - self.capacity]
        return loaded

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        count = max(1, int(limit))
        return list(self._items[-count:])

    def stats(self) -> dict:
        ok = sum(1 for e in self._items if e.ok)
        by_tool = Counter(e.tool for e in self._items)
        return {
            "total": len(self._items),
            "ok": ok,
            "failed": len(self._items) - ok,
            "by_tool": dict(by_tool),
        }

    def clear(self) -> None:
        self._items.clear()


class Quota:
    """按天计数的配额：0 或负数表示不限制。"""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    @staticmethod
    def today(when: float | None = None) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(when if when is not None else time.time()))

    def _key(self, day: str, tool: str) -> str:
        return f"{day}:{tool}"

    def used(self, tool: str = "total", *, when: float | None = None) -> int:
        return self._counts.get(self._key(self.today(when), tool), 0)

    def left(self, limit: int, *, when: float | None = None) -> int:
        limit = int(limit)
        if limit <= 0:
            return -1
        return max(0, limit - self.used("total", when=when))

    def bump(self, tool: str, *, when: float | None = None) -> int:
        day = self.today(when)
        self._counts[self._key(day, "total")] = self._counts.get(self._key(day, "total"), 0) + 1
        key = self._key(day, tool)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def snapshot(self, *, when: float | None = None) -> dict:
        day = self.today(when)
        prefix = f"{day}:"
        return {k[len(prefix):]: v for k, v in self._counts.items() if k.startswith(prefix)}
