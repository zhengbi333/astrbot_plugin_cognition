"""受限命令执行器：默认关闭，只允许白名单里的可执行文件。

为什么要这么严：聊天窗口后面是**群里的任何人**。一个不受限的 shell 等于把整台机器
的读写执行权交出去，所以这里：
- 默认 `enabled = False`；
- 只允许白名单里的**程序名**（不含路径分隔符，防止 `./evil.exe`）；
- 不用 `shell=True`，参数按列表传递（元字符不会被解释）；
- 工作目录锁在工作区，超时强杀，输出截断。
"""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Iterable

# 这些名字即便被写进白名单也一律拒绝（它们本身就是"任意执行"的入口）
_ALWAYS_DENY = {
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "bash", "bash.exe", "sh", "sh.exe", "zsh", "wsl", "wsl.exe",
    "python", "python.exe", "python3", "py", "node", "node.exe",
    "perl", "ruby", "php", "deno", "bun", "java", "javaw", "cscript", "wscript",
    "reg", "reg.exe", "schtasks", "schtasks.exe", "sc", "sc.exe",
    "net", "net.exe", "netsh", "netsh.exe", "taskkill", "taskkill.exe",
}


class ShellError(Exception):
    """命令执行失败（信息面向模型可读）。"""


def parse_allowlist(value: str | Iterable[str] | None) -> set[str]:
    """把配置里的白名单解析成小写集合（逗号/空白分隔）。"""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = [str(x) for x in value]
    else:
        items = [p for p in str(value).replace("，", ",").split(",")]
    out: set[str] = set()
    for item in items:
        name = item.strip().strip('"').lower()
        if name:
            out.add(name)
    return out


def _normalize_program(token: str) -> str:
    """把 `git` / `git.exe` 归一成小写程序名，用于白名单比对。"""
    base = os.path.basename(str(token or "").strip()).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base


def split_command(command: str) -> list[str]:
    """把命令行拆成参数列表（不经过 shell；Windows 路径的反斜杠按字面处理）。"""
    text = str(command or "").strip()
    if not text:
        raise ShellError("没有给命令")
    if "\n" in text or "\r" in text:
        raise ShellError("一次只执行一条命令（不要换行）")
    try:
        parts = shlex.split(text, posix=False)
    except ValueError as exc:
        raise ShellError(f"命令解析失败：{exc}") from exc
    parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return [p for p in parts if p != ""]


async def run_command(
    command: str,
    *,
    allowlist: set[str],
    cwd: Path,
    timeout: float = 15.0,
    max_output_chars: int = 4000,
) -> tuple[int, str]:
    """执行白名单内的命令，返回 (退出码, 输出文本)。"""
    argv = split_command(command)
    program_token = argv[0]
    if any(sep in program_token for sep in ("/", "\\")):
        raise ShellError("程序名不要带路径（只写名字，如 git）")
    name = _normalize_program(program_token)
    if name in _ALWAYS_DENY:
        raise ShellError(f"拒绝执行：{name} 属于高危命令（可在白名单里也无效）")
    if name not in allowlist:
        allowed = "、".join(sorted(allowlist)) or "（空）"
        raise ShellError(f"命令未在白名单内：{name}；当前允许：{allowed}")

    workdir = Path(cwd)
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ShellError(f"找不到程序：{argv[0]}") from exc
    except OSError as exc:
        raise ShellError(f"启动失败：{exc}") from exc

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, float(timeout)))
        code = int(proc.returncode or 0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(proc.wait(), return_exceptions=True)
        raise ShellError(f"命令超时（>{timeout:g} 秒），已终止")

    text = _decode(out or b"")
    if len(text) > max_output_chars:
        text = text[:max_output_chars] + f"\n…（输出过长，已截断到 {max_output_chars} 字）"
    return code, text.strip()


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "utf-16-le", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
