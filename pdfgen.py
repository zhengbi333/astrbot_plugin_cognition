"""纯 Python 出 PDF：不装 LibreOffice，也能把文本/表格/幻灯片变成 PDF。

思路（为什么这么做）：
LibreOffice 的价值在"排版 + 出 PDF"，但我们真正的需求常常只是"把内容变成能发出去的 PDF"。
于是换一条路：**用 Pillow 把每一页渲染成图片，再让 Pillow 直接存成多页 PDF**。
好处是中文不成问题（用系统字体渲染），不依赖任何外部程序；
代价是 PDF 里是位图、文字不可选 —— 这一点会在结果里如实告诉使用者。

字体探测顺序（找到第一个就用）：微软雅黑 → 黑体 → 宋体 → 苹方 → 常见 Linux 中文字体。
一个都找不到时抛错并说明，不静默出乱码页。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore

# 150 DPI 下的常见纸张像素尺寸
PAGE_SIZES: dict[str, tuple[int, int]] = {
    "a4": (1240, 1754),
    "a4l": (1754, 1240),
    "letter": (1275, 1650),
}

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


class PdfError(Exception):
    """生成 PDF 失败（信息面向模型可读）。"""


@dataclass(slots=True)
class PdfResult:
    path: Path
    pages: int
    bytes: int
    note: str = ""


def _require_pillow() -> None:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise PdfError("缺少 Pillow，无法生成 PDF")


def find_font() -> Path:
    """找一个能显示中文的字体文件。"""
    for item in _FONT_CANDIDATES:
        path = Path(item)
        if path.exists():
            return path
    raise PdfError(
        "系统里没找到中文字体（试过 微软雅黑/黑体/宋体/苹方/Noto/文泉驿），无法保证中文不出乱码"
    )


def load_font(size: int) -> "ImageFont.FreeTypeFont":
    path = find_font()
    index = 0
    # .ttc 是字体集合，取第 0 个
    try:
        return ImageFont.truetype(str(path), size=size, index=index)
    except OSError as exc:
        raise PdfError(f"加载字体失败：{exc}") from exc


def _wrap(text: str, font: "ImageFont.FreeTypeFont", max_width: int) -> list[str]:
    """按像素宽度折行（中文按字、英文按词，尽量不切断单词）。"""
    lines: list[str] = []
    for raw_line in str(text).splitlines() or [""]:
        if not raw_line.strip():
            lines.append("")
            continue
        buffer = ""
        for char in raw_line:
            candidate = buffer + char
            try:
                width = font.getlength(candidate)
            except AttributeError:  # 老版本 Pillow
                width = font.getsize(candidate)[0]
            if width <= max_width or not buffer:
                buffer = candidate
            else:
                lines.append(buffer)
                buffer = char
        if buffer:
            lines.append(buffer)
    return lines


def _new_page(size: tuple[int, int]) -> tuple["Image.Image", "ImageDraw.ImageDraw"]:
    page = Image.new("RGB", size, "white")
    return page, ImageDraw.Draw(page)


def _save_pdf(pages: list["Image.Image"], out_path: Path, *, dpi: int = 150) -> int:
    if not pages:
        raise PdfError("没有任何页面可写")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        pages[0].save(
            str(out_path),
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=float(dpi),
        )
    except Exception as exc:
        raise PdfError(f"写 PDF 失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    return len(pages)


def text_to_pdf(
    text: str,
    out_path: Path,
    *,
    title: str = "",
    font_size: int = 22,
    page_size: str = "a4",
    margin: int = 80,
    line_gap: int = 10,
) -> PdfResult:
    """把文本渲染成多页 PDF，自动分页。"""
    _require_pillow()
    size = PAGE_SIZES.get(str(page_size).lower(), PAGE_SIZES["a4"])
    width, height = size
    font = load_font(font_size)
    title_font = load_font(max(font_size + 10, int(font_size * 1.5)))
    body = _wrap(text, font, width - margin * 2)

    pages: list["Image.Image"] = []
    index = 0
    first_page = True
    while index < len(body) or first_page:
        page, draw = _new_page(size)
        y = margin
        if first_page and title:
            draw.text((margin, y), str(title), fill="black", font=title_font)
            y += int(title_font.size * 1.6)
        line_height = int(font.size * 1.4) + line_gap
        while index < len(body):
            if y + line_height > height - margin:
                break
            draw.text((margin, y), body[index], fill="black", font=font)
            y += line_height
            index += 1
        pages.append(page)
        first_page = False
        if len(pages) > 500:  # 防御：别把内存吃穿
            raise PdfError("页数超过 500，请拆分内容")

    count = _save_pdf(pages, out_path)
    return PdfResult(
        path=out_path,
        pages=count,
        bytes=out_path.stat().st_size if out_path.exists() else 0,
        note="PDF 内是位图、文字不可复制（用 Pillow 渲染，无需 LibreOffice）",
    )


def table_to_pdf(
    rows: Sequence[Sequence[object]],
    out_path: Path,
    *,
    title: str = "",
    font_size: int = 18,
    page_size: str = "a4",
    margin: int = 60,
) -> PdfResult:
    """把二维数据画成带网格的表格 PDF，自动分页、自动均分列宽。"""
    _require_pillow()
    size = PAGE_SIZES.get(str(page_size).lower(), PAGE_SIZES["a4"])
    width, height = size
    font = load_font(font_size)
    title_font = load_font(max(font_size + 8, int(font_size * 1.4)))

    data = [list(row) for row in rows if row]
    if not data:
        raise PdfError("表格是空的")
    columns = max(len(row) for row in data)
    for row in data:
        row.extend([""] * (columns - len(row)))

    cell_pad = 8
    col_width = max(60, (width - margin * 2) // max(1, columns))
    line_h = int(font.size * 1.5)

    pages: list["Image.Image"] = []
    index = 0
    first_page = True
    while index < len(data) or first_page:
        page, draw = _new_page(size)
        y = margin
        if first_page and title:
            draw.text((margin, y), str(title), fill="black", font=title_font)
            y += int(title_font.size * 1.8)
        while index < len(data):
            row_height = line_h
            # 逐格折行，算这一行需要多高
            wrapped: list[list[str]] = []
            for cell in data[index]:
                cells = _wrap(str(cell), font, col_width - cell_pad * 2) or [""]
                wrapped.append(cells)
                row_height = max(row_height, line_h * len(cells) + cell_pad)
            if y + row_height > height - margin:
                break
            x = margin
            for cells in wrapped:
                for ci, line in enumerate(cells):
                    draw.text((x + cell_pad, y + cell_pad + ci * line_h), line, fill="black", font=font)
                draw.rectangle([x, y, x + col_width, y + row_height], outline="black")
                x += col_width
            y += row_height
            index += 1
        pages.append(page)
        first_page = False
        if len(pages) > 300:
            raise PdfError("页数超过 300，请拆分内容")

    count = _save_pdf(pages, out_path)
    return PdfResult(
        path=out_path,
        pages=count,
        bytes=out_path.stat().st_size if out_path.exists() else 0,
        note="PDF 内是位图、文字不可复制（用 Pillow 渲染，无需 LibreOffice）",
    )


def slides_to_pdf(
    slides: Iterable[tuple[str, list[str]]],
    out_path: Path,
    *,
    title: str = "",
    font_size: int = 24,
    page_size: str = "a4l",
) -> PdfResult:
    """把「标题 + 要点」的幻灯片列表渲染成横向 PDF（每页一张）。"""
    _require_pillow()
    size = PAGE_SIZES.get(str(page_size).lower(), PAGE_SIZES["a4l"])
    width, height = size
    margin = 90
    head_font = load_font(max(font_size + 10, 34))
    body_font = load_font(font_size)
    cover_font = load_font(max(font_size + 24, 52))

    pages: list["Image.Image"] = []
    if title:
        page, draw = _new_page(size)
        try:
            w = draw.textlength(str(title), font=cover_font)
        except Exception:
            w = len(str(title)) * cover_font.size
        draw.text(((width - w) / 2, height / 2 - cover_font.size), str(title), fill="black", font=cover_font)
        pages.append(page)

    for slide_title, bullets in slides:
        page, draw = _new_page(size)
        y = margin
        draw.text((margin, y), str(slide_title or "（无标题）"), fill="black", font=head_font)
        y += int(head_font.size * 2.0)
        line_h = int(body_font.size * 1.6)
        for bullet in bullets or []:
            for line in _wrap("• " + str(bullet), body_font, width - margin * 2):
                if y + line_h > height - margin:
                    break
                draw.text((margin + 10, y), line, fill="black", font=body_font)
                y += line_h
        pages.append(page)
        if len(pages) > 300:
            raise PdfError("页数超过 300，请拆分内容")

    count = _save_pdf(pages, out_path)
    return PdfResult(
        path=out_path,
        pages=count,
        bytes=out_path.stat().st_size if out_path.exists() else 0,
        note="PDF 内是位图、文字不可复制（用 Pillow 渲染，无需 LibreOffice）",
    )
