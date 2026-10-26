"""在工作区里执行 Python 脚本：把"固定菜单"变成"任意任务"。

为什么需要它：任务是无穷的，工具是有限的。真实世界里"帮我做个 xxx"绝大多数落在
已有的库上（pandas 算、python-docx 写、python-pptx 出幻灯片、Pillow 处理图），
缺的从来不是"再多一个工具"，而是"能不能当场写一段代码把这件事做完"。

安全边界（必须清楚）：
- **只允许 owner 名单里的账号触发**（默认就是使用者本人）——群友说破天也调不动；
- 脚本工作目录锁死在工作区，只能读写工作区内的文件；
- 超时强杀、输出截断、脚本大小上限；
- 每次执行记入审计（含代码首行与哈希，便于事后追查）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .textutil import truncate_keep_ends


class ExecError(Exception):
    """脚本执行失败（信息面向模型可读）。"""


@dataclass(slots=True)
class ExecResult:
    ok: bool
    code: int
    output: str
    seconds: float
    script: str


def parse_owners(value: str | list | tuple | set | None) -> set[str]:
    """解析 owner 名单（逗号/空白分隔的账号 ID）。"""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = [str(x) for x in value]
    else:
        items = str(value).replace("，", ",").replace(";", ",").replace(" ", ",").split(",")
    return {item.strip() for item in items if item.strip()}


def is_owner(sender_id: str, owners: set[str]) -> bool:
    """sender 是否在 owner 名单里（空名单=无人可用，绝不等于"所有人"）。"""
    if not owners:
        return False
    return str(sender_id or "").strip() in owners


def script_digest(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8", errors="replace")).hexdigest()[:12]


def first_line(code: str, limit: int = 60) -> str:
    for line in str(code or "").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text[:limit]
    return "（只有注释或空）"


async def run_python(
    code: str,
    *,
    cwd: Path,
    timeout: float = 30.0,
    max_output_chars: int = 4000,
    max_code_chars: int = 20000,
    python: str | None = None,
) -> ExecResult:
    """把代码写成临时脚本并执行，返回 stdout+stderr 合并后的文本。"""
    source = str(code or "")
    if not source.strip():
        raise ExecError("没有给代码")
    if len(source) > max_code_chars:
        raise ExecError(f"代码太长（{len(source)} 字符，上限 {max_code_chars}）")

    workdir = Path(cwd)
    workdir.mkdir(parents=True, exist_ok=True)
    tmp_dir = workdir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_dir / f"run_{script_digest(source)}.py"
    script.write_text(source, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 让脚本能 import 到自己（可选），但不要把 AstrBot 的私有配置塞进去
    env.pop("ASTRBOT_CONFIG", None)

    interpreter = python or sys.executable or "python"
    started = asyncio.get_event_loop().time()
    try:
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            "-X",
            "utf8",
            str(script),
            cwd=str(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ExecError("找不到 Python 解释器") from exc
    except OSError as exc:
        raise ExecError(f"启动脚本失败：{exc}") from exc

    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, float(timeout)))
        code_int = int(proc.returncode or 0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(proc.wait(), return_exceptions=True)
        elapsed = asyncio.get_event_loop().time() - started
        raise ExecError(f"脚本超时（>{timeout:g} 秒，已运行 {elapsed:.1f}s），已强杀")
    elapsed = asyncio.get_event_loop().time() - started

    text = (raw or b"").decode("utf-8", errors="replace")
    # 0.5.0：保留首尾——异常堆栈的最后一行、print 的结果常常就在结尾
    text = truncate_keep_ends(text, max_output_chars)
    return ExecResult(ok=code_int == 0, code=code_int, output=text.strip(), seconds=elapsed, script=script.name)
