"""并行执行：把互不相干的几段脚本同时跑，再汇总结果。

为什么需要：真实的活经常是"三件事互不依赖"（抓三个网站、处理三批文件、跑三种统计）。
串行跑要等三倍时间；而单个脚本里自己开线程又绕过了我们的超时与输出治理。
这里给一层受控的并发：

- 最多 `max_concurrent`（默认 3）路同时跑，超出的排队；
- 每路有独立超时与输出上限，单路失败不影响其它路；
- 结果按提交顺序返回，带每个人自己的状态、耗时、输出摘要；
- 所有脚本写在 `<工作区>/parallel/<批次>/` 下，便于事后查。

不做的事：不在这里做"多轮对话/子代理"（那是另一套东西），只做进程级并行。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .textutil import truncate_keep_ends


class ParallelError(Exception):
    """并行执行失败（信息面向模型可读）。"""


@dataclass(slots=True)
class JobResult:
    name: str
    ok: bool
    code: int
    output: str
    seconds: float


def _safe_name(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", str(text or "")).strip("-")
    return (cleaned or fallback)[:24]


def parse_tasks(raw: str | list) -> list[dict]:
    """解析任务列表：支持 JSON 字符串或已经是 list。"""
    data = raw
    if isinstance(raw, str):
        text = str(raw).strip()
        if not text:
            raise ParallelError("没有给任务")
        for candidate in (text, text.replace("\\", "/")):
            try:
                data = json.loads(candidate)
                break
            except (ValueError, TypeError):
                continue
        else:
            raise ParallelError("任务不是合法 JSON（要一个数组，每项含 name 与 code）")
    if not isinstance(data, list) or not data:
        raise ParallelError("任务要写成数组，至少一个")
    tasks: list[dict] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ParallelError(f"第 {index + 1} 个任务不是对象")
        code = str(item.get("code") or "")
        if not code.strip():
            raise ParallelError(f"第 {index + 1} 个任务没有 code")
        tasks.append({
            "name": _safe_name(item.get("name") or f"任务{index + 1}", f"job{index + 1}"),
            "code": code,
            "timeout": float(item.get("timeout") or 0) or 0.0,
        })
    return tasks


async def _run_one(
    *, name: str, code: str, path: Path, cwd: Path, timeout: float,
    max_output_chars: int, env: dict,
) -> JobResult:
    started = time.monotonic()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    except OSError as exc:
        return JobResult(name=name, ok=False, code=-1, output=f"写脚本失败：{exc}", seconds=0.0)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable or "python", "-u", "-X", "utf8", str(path),
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        return JobResult(name=name, ok=False, code=-1, output=f"启动失败：{exc}", seconds=0.0)

    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, timeout))
        code_int = int(proc.returncode or 0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(proc.wait(), return_exceptions=True)
        elapsed = time.monotonic() - started
        return JobResult(name=name, ok=False, code=-1,
                         output=f"超时（>{timeout:g} 秒），已强杀", seconds=elapsed)

    elapsed = time.monotonic() - started
    text = truncate_keep_ends((raw or b"").decode("utf-8", errors="replace"), max_output_chars)
    return JobResult(name=name, ok=code_int == 0, code=code_int, output=text.strip(), seconds=elapsed)


async def run_many(
    tasks: list[dict],
    *,
    cwd: Path,
    timeout: float = 60.0,
    max_output_chars: int = 2000,
    max_concurrent: int = 3,
    batch_dir: Path | None = None,
) -> list[JobResult]:
    """并行跑一批任务，按提交顺序返回结果。"""
    if not tasks:
        raise ParallelError("没有可跑的任务")
    limit = max(1, min(6, int(max_concurrent or 3)))
    semaphore = asyncio.Semaphore(limit)

    base = Path(batch_dir) if batch_dir else Path(cwd) / "parallel"
    stamp = time.strftime("%H%M%S", time.localtime())
    batch = base / f"{stamp}-{secrets.token_hex(2)}"
    batch.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("ASTRBOT_CONFIG", None)

    async def _guarded(index: int, task: dict) -> JobResult:
        async with semaphore:
            return await _run_one(
                name=task["name"],
                code=task["code"],
                path=batch / f"{index + 1:02d}-{task['name']}.py",
                cwd=Path(cwd),
                timeout=float(task.get("timeout") or timeout),
                max_output_chars=max_output_chars,
                env=env,
            )

    return list(await asyncio.gather(*(_guarded(i, t) for i, t in enumerate(tasks))))


def summarize(results: list[JobResult], *, per_output: int = 400) -> str:
    """把结果拼成给模型看的一段话（失败的要显眼）。"""
    lines: list[str] = []
    ok_count = sum(1 for r in results if r.ok)
    lines.append(f"共 {len(results)} 路，成功 {ok_count}，失败 {len(results) - ok_count}。")
    for index, result in enumerate(results, 1):
        flag = "✓" if result.ok else "✗"
        head = f"{index}. {flag} {result.name}（{result.seconds:.1f}s"
        head += f"，退出码 {result.code}）" if not result.ok else "）"
        lines.append(head)
        body = result.output or "（没有输出）"
        if len(body) > per_output:
            body = body[:per_output].rstrip() + "…"
        for line in body.splitlines()[:12]:
            lines.append("   " + line)
    return "\n".join(lines)
