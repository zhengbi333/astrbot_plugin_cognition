"""后台任务：脚本丢进去立刻返回，之后按需查输出、可随时停止。

为什么需要：前台执行有超时上限（120 秒），批量处理、爬取、渲染这类活做不完。
这里用"子进程 + 日志文件"的方式做最小的作业系统：

- `start()` 立即返回 task_id，不阻塞对话；
- 输出**直接重定向到文件**，不会撑爆内存；
- `status()` 读日志尾部（保首尾），并报告是否结束、退出码；
- `stop()` 强杀；
- 并发上限与存活超时（超龄自动清理），避免进程泄漏；
- `shutdown()` 在插件停止时清干净。

注意：进程结束状态靠一个内部 asyncio 任务 `await proc.wait()` 来刷新，
所以 `status()` 可以在不阻塞的情况下读到准确的 `returncode`。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .textutil import tail_lines


class TaskError(Exception):
    """后台任务操作失败（信息面向模型可读）。"""


def _now() -> float:
    return time.time()


def new_task_id() -> str:
    """短、可读、不重复的任务号：时分秒 + 3 字节随机。"""
    return time.strftime("%H%M%S", time.localtime()) + "-" + secrets.token_hex(2)


@dataclass
class TaskRecord:
    task_id: str
    script: Path
    log: Path
    started: float
    proc: asyncio.subprocess.Process | None = None
    log_handle: object | None = None
    returncode: int | None = None
    killer: asyncio.Task | None = None
    note: str = ""

    @property
    def running(self) -> bool:
        return self.returncode is None and self.proc is not None

    @property
    def seconds(self) -> float:
        return max(0.0, _now() - self.started)


class TaskManager:
    """最小可用的后台作业表。"""

    def __init__(
        self,
        root: Path,
        *,
        max_concurrent: int = 2,
        max_age_seconds: float = 1800.0,
        log_tail_chars: int = 2000,
        python: str | None = None,
        max_kept: int = 20,
    ) -> None:
        self.root = Path(root)
        self.dir = self.root / ".tasks"
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_age_seconds = max(30.0, float(max_age_seconds))
        self.log_tail_chars = max(200, int(log_tail_chars))
        self.python = python or sys.executable or "python"
        self.max_kept = max(3, int(max_kept))
        self._tasks: dict[str, TaskRecord] = {}

    # ------------------------------------------------------------ 查询
    def running_count(self) -> int:
        return sum(1 for record in self._tasks.values() if record.running)

    def list_tasks(self) -> list[TaskRecord]:
        return sorted(self._tasks.values(), key=lambda r: r.started, reverse=True)

    def get(self, task_id: str) -> TaskRecord:
        key = str(task_id or "").strip()
        record = self._tasks.get(key)
        if record is None:
            known = "、".join(r.task_id for r in self.list_tasks()[:5]) or "（没有）"
            raise TaskError(f"没有这个任务：{key or '（空）'}；最近的：{known}")
        return record

    def tail(self, record: TaskRecord, chars: int | None = None) -> str:
        limit = self.log_tail_chars if chars is None else max(100, int(chars))
        try:
            if not record.log.exists():
                return "（还没有输出）"
            data = record.log.read_bytes()
            if len(data) > limit * 4:  # 粗略裁一下再解码，避免读整份大日志
                data = data[-(limit * 4):]
            text = data.decode("utf-8", errors="replace")
        except OSError as exc:
            return f"（读日志失败：{exc}）"
        if not text.strip():
            return "（还没有输出）"
        return tail_lines(text, 40, max_chars=limit)

    # ------------------------------------------------------------ 生命周期
    async def start(self, code: str, *, max_code_chars: int = 20000) -> TaskRecord:
        source = str(code or "")
        if not source.strip():
            raise TaskError("没有给代码")
        if len(source) > max_code_chars:
            raise TaskError(f"代码太长（{len(source)} 字符，上限 {max_code_chars}）")
        await self.cleanup()
        if self.running_count() >= self.max_concurrent:
            raise TaskError(
                f"同时最多跑 {self.max_concurrent} 个后台任务，先等正在跑的结束，或者把旧的停掉"
            )

        self.dir.mkdir(parents=True, exist_ok=True)
        task_id = new_task_id()
        script = self.dir / f"{task_id}.py"
        log = self.dir / f"{task_id}.log"
        script.write_text(source, encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.pop("ASTRBOT_CONFIG", None)

        handle = open(log, "wb")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.python, "-X", "utf8", str(script),
                cwd=str(self.root),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError as exc:
            handle.close()
            raise TaskError("找不到 Python 解释器") from exc
        except OSError as exc:
            handle.close()
            raise TaskError(f"启动后台任务失败：{exc}") from exc

        record = TaskRecord(task_id=task_id, script=script, log=log, started=_now(),
                            proc=proc, log_handle=handle)
        record.killer = asyncio.ensure_future(self._watch(record))
        self._tasks[task_id] = record
        self._trim()
        return record

    async def _watch(self, record: TaskRecord) -> None:
        """等进程结束，记录退出码并关掉日志句柄。"""
        try:
            if record.proc is not None:
                record.returncode = await record.proc.wait()
        except Exception:
            record.returncode = -1
        finally:
            try:
                if record.log_handle is not None:
                    record.log_handle.close()  # type: ignore[attr-defined]
                    record.log_handle = None
            except Exception:
                pass

    async def stop(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        if not record.running:
            return record
        try:
            record.proc.kill()  # type: ignore[union-attr]
        except ProcessLookupError:
            pass
        except Exception as exc:
            raise TaskError(f"停止失败：{exc}") from exc
        record.note = "被手动停止"
        try:
            await asyncio.wait_for(record.proc.wait(), timeout=5)  # type: ignore[union-attr]
        except Exception:
            pass
        self._finalize(record)
        return record

    @staticmethod
    def _finalize(record: TaskRecord) -> None:
        """把进程的真实退出码写回记录（强杀后立刻生效，不必等 _watch）。"""
        if record.returncode is not None:
            return
        proc = record.proc
        if proc is None:
            record.returncode = -1
            return
        record.returncode = proc.returncode if proc.returncode is not None else -9

    async def cleanup(self) -> list[str]:
        """强杀超龄任务，清掉过多的历史记录。返回被清理的 id 列表。"""
        killed: list[str] = []
        for record in list(self._tasks.values()):
            if record.running and record.seconds > self.max_age_seconds:
                try:
                    record.proc.kill()  # type: ignore[union-attr]
                except Exception:
                    pass
                record.note = f"超过存活上限 {int(self.max_age_seconds)} 秒，已强杀"
                killed.append(record.task_id)
                try:
                    await asyncio.wait_for(record.proc.wait(), timeout=3)  # type: ignore[union-attr]
                except Exception:
                    pass
                self._finalize(record)
        self._trim()
        return killed

    def _trim(self) -> None:
        if len(self._tasks) <= self.max_kept:
            return
        finished = [r for r in self.list_tasks() if not r.running]
        for record in finished[self.max_kept // 2:]:
            self._tasks.pop(record.task_id, None)

    async def shutdown(self) -> None:
        """插件停止：杀掉所有还在跑的任务。"""
        for record in list(self._tasks.values()):
            if record.running:
                try:
                    record.proc.kill()  # type: ignore[union-attr]
                except Exception:
                    pass
            try:
                if record.log_handle is not None:
                    record.log_handle.close()  # type: ignore[attr-defined]
                    record.log_handle = None
            except Exception:
                pass
        self._tasks.clear()
