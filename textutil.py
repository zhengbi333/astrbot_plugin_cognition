"""文本小工具：长文本截断时保留首尾。

工具输出里最关键的东西常在结尾（异常堆栈最后一行、"共 N 条"、保存路径），
只砍尾巴会让模型看不到结果、误判成没做成。所以统一"前 70% + 后 30%"。
"""

from __future__ import annotations

OMIT_TEMPLATE = "\n…（中间省略 {n} 字）…\n"


def truncate_keep_ends(text: str, limit: int = 4000, head_ratio: float = 0.7) -> str:
    """超过 limit 时保留头部与尾部，中间用省略标记连起来。"""
    raw = str(text or "")
    if limit <= 0 or len(raw) <= limit:
        return raw
    ratio = max(0.1, min(0.9, float(head_ratio or 0.7)))
    head_len = max(1, int(limit * ratio))
    tail_len = max(1, limit - head_len)
    omitted = len(raw) - head_len - tail_len
    if omitted <= 0:
        return raw[:limit]
    return raw[:head_len] + OMIT_TEMPLATE.format(n=omitted) + raw[-tail_len:]


def tail_lines(text: str, count: int = 20, *, max_chars: int = 2000) -> str:
    """取最后 N 行（用于日志尾部），仍然受字数上限约束。"""
    lines = str(text or "").splitlines()
    picked = lines[-max(1, int(count)):]
    joined = "\n".join(picked)
    return truncate_keep_ends(joined, max_chars)
