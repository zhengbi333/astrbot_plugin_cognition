"""网络工具：抓网页正文、联网搜索、下载文件。

安全要点：
- 只允许 http/https；
- **拒绝访问内网/本机地址**（127./10./172.16-31./192.168./169.254./::1/localhost 等）——
  否则群里任何人都能借 Bot 去读你自己的 WebUI、面板或局域网设备（SSRF）；
- 下载/抓取都有体积上限与超时，避免把大文件或慢连接塞爆上下文。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:  # 优先用 httpx（AstrBot 自带）
    import httpx as _httpx
except Exception:  # pragma: no cover - 环境缺库时给出可读错误
    _httpx = None


class WebError(Exception):
    """网络操作失败（信息面向模型可读）。"""


_BLOCKED_HOSTNAMES = (
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
)
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BREAK_RE = re.compile(r"<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\u00a0]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def _require_httpx() -> Any:
    if _httpx is None:
        raise WebError("缺少 httpx 依赖，无法联网")
    return _httpx


def _is_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def check_url(url: str) -> str:
    """校验并规范化 URL；不通过抛 WebError。"""
    raw = str(url or "").strip()
    if not raw:
        raise WebError("没有给网址")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise WebError("只支持 http/https 网址")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise WebError("网址里没有主机名")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise WebError("拒绝访问本机/内网地址")
    if _is_private_ip(host):
        raise WebError("拒绝访问本机/内网地址")
    # 域名解析后再查一次，挡住"解析到内网"的情况（DNS 指向 127.x 等）
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        for info in infos[:4]:
            ip = str(info[4][0])
            if _is_private_ip(ip):
                raise WebError("该域名解析到内网地址，已拒绝")
    except WebError:
        raise
    except OSError:
        pass  # 解析失败交给真正的请求去报错
    return raw


def html_to_text(html: str) -> str:
    """把 HTML 粗加工成可读文本（去脚本/样式/标签，保留基本分段）。"""
    text = _SCRIPT_STYLE_RE.sub(" ", str(html or ""))
    text = _COMMENT_RE.sub(" ", text)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    )
    text = _SPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _BLANKS_RE.sub("\n\n", text).strip()


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    title: str
    text: str
    bytes_read: int


async def fetch_text(
    url: str,
    *,
    timeout: float = 20.0,
    max_bytes: int = 2 * 1024 * 1024,
    max_chars: int = 4000,
    user_agent: str = "",
) -> FetchResult:
    """抓取网页并转成正文文本。"""
    httpx = _require_httpx()
    target = check_url(url)
    headers = {
        "User-Agent": user_agent
        or "Mozilla/5.0 (compatible; AstrBotToolbox/1.0; +https://github.com/zhengbi333)"
    }
    collected = bytearray()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout, headers=headers
        ) as client:
            async with client.stream("GET", target) as resp:
                if resp.status_code >= 400:
                    raise WebError(f"网站返回 {resp.status_code}")
                ctype = str(resp.headers.get("content-type") or "").lower()
                async for chunk in resp.aiter_bytes():
                    collected.extend(chunk)
                    if len(collected) >= max_bytes:
                        break
                status = resp.status_code
                final_url = str(resp.url)
    except WebError:
        raise
    except Exception as exc:
        raise WebError(f"抓取失败：{type(exc).__name__}: {str(exc)[:120]}") from exc

    charset = "utf-8"
    match = re.search(r"charset=([\w\-]+)", ctype)
    if match:
        charset = match.group(1)
    try:
        body = bytes(collected).decode(charset, errors="replace")
    except LookupError:
        body = bytes(collected).decode("utf-8", errors="replace")

    if "html" in ctype or "<html" in body[:2000].lower():
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        title = html_to_text(title_match.group(1)) if title_match else ""
        text = html_to_text(body)
    else:
        title = ""
        text = body.strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    if truncated:
        text += f"\n…（正文过长，已截断到 {max_chars} 字）"
    return FetchResult(url=final_url, status=status, title=title[:120], text=text, bytes_read=len(collected))


async def tavily_search(
    query: str, *, api_key: str, max_results: int = 5, timeout: float = 20.0
) -> str:
    """用 Tavily 搜索并整理成可读要点。"""
    httpx = _require_httpx()
    key = str(api_key or "").strip()
    if not key:
        raise WebError("没有配置搜索 API Key（Tavily）")
    question = str(query or "").strip()
    if not question:
        raise WebError("没有给搜索词")
    payload = {
        "api_key": key,
        "query": question,
        "search_depth": "basic",
        "max_results": max(1, min(10, int(max_results or 5))),
        "include_answer": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            if resp.status_code >= 400:
                raise WebError(f"搜索接口返回 {resp.status_code}")
            data = resp.json()
    except WebError:
        raise
    except Exception as exc:
        raise WebError(f"搜索失败：{type(exc).__name__}: {str(exc)[:120]}") from exc

    lines: list[str] = []
    answer = str(data.get("answer") or "").strip()
    if answer:
        lines.append(f"概述：{answer[:400]}")
    results = data.get("results") or []
    for index, item in enumerate(results[: int(max_results or 5)], 1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        lines.append(f"{index}. {title}\n   {url}\n   {content[:240]}")
    if not lines:
        lines.append("（没有搜到结果）")
    return "\n".join(lines)


async def download(
    url: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = 50 * 1024 * 1024,
    user_agent: str = "",
) -> tuple[bytes, str, str]:
    """下载文件，返回 (字节内容, content-type, 推断文件名)。"""
    httpx = _require_httpx()
    target = check_url(url)
    headers = {"User-Agent": user_agent or "Mozilla/5.0 (compatible; AstrBotToolbox/1.0)"}
    collected = bytearray()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
            async with client.stream("GET", target) as resp:
                if resp.status_code >= 400:
                    raise WebError(f"下载失败：网站返回 {resp.status_code}")
                declared = int(resp.headers.get("content-length") or 0)
                if declared and declared > max_bytes:
                    raise WebError(f"文件太大（服务器声明 {declared} 字节，上限 {max_bytes}）")
                ctype = str(resp.headers.get("content-type") or "").split(";")[0].strip()
                async for chunk in resp.aiter_bytes():
                    collected.extend(chunk)
                    if len(collected) > max_bytes:
                        raise WebError(f"文件超过上限 {max_bytes} 字节，已中止")
    except WebError:
        raise
    except Exception as exc:
        raise WebError(f"下载失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    return bytes(collected), ctype, ""
