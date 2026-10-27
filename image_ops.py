"""图片处理：缩放、压缩、水印、格式转换。

用 Pillow 补"没有图像软件"的缺口。所有路径由调用方（Workspace）先校验，
本模块只负责图像操作，不判断路径是否合法。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore

_OPS = ("resize", "compress", "watermark", "convert")


class ImageOpError(Exception):
    """图片操作失败（信息面向模型可读）。"""


@dataclass(slots=True)
class ImageResult:
    path: Path
    width: int
    height: int
    bytes: int
    note: str = ""


def _require() -> None:
    if Image is None:
        raise ImageOpError("缺少 Pillow，无法处理图片")


def _open(path: Path):
    try:
        image = Image.open(str(path))
        image.load()
        return image
    except Exception as exc:
        raise ImageOpError(f"打不开图片：{type(exc).__name__}: {str(exc)[:120]}") from exc


def _out_path(source: Path, out: str, *, suffix: str) -> Path:
    if str(out or "").strip():
        return Path(out)
    return source.with_name(f"{source.stem}{suffix}{source.suffix or '.png'}")


def _result(path: Path, note: str = "") -> ImageResult:
    with Image.open(str(path)) as img:
        width, height = img.size
    return ImageResult(path=path, width=width, height=height, bytes=path.stat().st_size, note=note)


def resize(source: Path, *, width: int = 0, height: int = 0, out: str = "") -> ImageResult:
    """按给定宽/高缩放；只给一边时按比例算另一边。"""
    _require()
    image = _open(source)
    w, h = image.size
    target_w = int(width or 0)
    target_h = int(height or 0)
    if target_w <= 0 and target_h <= 0:
        raise ImageOpError("至少给一个宽度或高度")
    if target_w <= 0:
        target_w = max(1, round(w * target_h / h))
    elif target_h <= 0:
        target_h = max(1, round(h * target_w / w))
    if target_w > 20000 or target_h > 20000:
        raise ImageOpError("目标尺寸太大（超过 20000 像素）")
    target = _out_path(source, out, suffix="_resized")
    try:
        image.resize((target_w, target_h), Image.LANCZOS).save(str(target))
    except Exception as exc:
        raise ImageOpError(f"缩放失败：{exc}") from exc
    return _result(target, f"{w}x{h} → {target_w}x{target_h}")


def compress(source: Path, *, quality: int = 70, out: str = "") -> ImageResult:
    """有损压缩（JPEG/WebP 有效；PNG 会转存为 JPEG 才有明显效果）。"""
    _require()
    image = _open(source)
    q = max(10, min(95, int(quality or 70)))
    suffix = source.suffix.lower()
    target = _out_path(source, out, suffix="_min")
    if str(out or "").strip():
        target = Path(out)
    else:
        target = source.with_name(f"{source.stem}_min.jpg")
    try:
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, "white")
            background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
            image = background
        image.convert("RGB").save(str(target), "JPEG", quality=q, optimize=True)
    except Exception as exc:
        raise ImageOpError(f"压缩失败：{exc}") from exc
    before = source.stat().st_size
    after = target.stat().st_size
    return _result(target, f"质量 {q}；{before // 1024}KB → {after // 1024}KB")


def watermark(source: Path, *, text: str, out: str = "", position: str = "br") -> ImageResult:
    """在右下角（或指定角）加水印文字。"""
    _require()
    content = str(text or "").strip()
    if not content:
        raise ImageOpError("水印文字是空的")
    image = _open(source).convert("RGBA")
    draw = ImageDraw.Draw(image)
    size = max(16, image.size[0] // 30)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size=size, index=0)
    except Exception:
        font = ImageFont.load_default()
    try:
        box = draw.textbbox((0, 0), content, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
    except Exception:
        tw, th = len(content) * size, size
    pad = max(12, size // 2)
    pos = str(position or "br").lower()
    x = pad if "l" in pos else image.size[0] - tw - pad
    y = pad if "t" in pos else image.size[1] - th - pad
    draw.text((x, y), content, font=font, fill=(255, 255, 255, 180))
    target = _out_path(source, out, suffix="_wm")
    try:
        image.convert("RGB").save(str(target))
    except Exception as exc:
        raise ImageOpError(f"加水印失败：{exc}") from exc
    return _result(target, f"水印「{content}」")


def convert(source: Path, *, fmt: str = "png", out: str = "") -> ImageResult:
    """转换格式（png/jpg/webp/bmp）。"""
    _require()
    kind = re.sub(r"[^a-z]", "", str(fmt or "png").lower()) or "png"
    if kind == "jpg":
        kind = "jpeg"
    if kind not in ("png", "jpeg", "webp", "bmp"):
        raise ImageOpError("只支持 png / jpg / webp / bmp")
    image = _open(source)
    ext = "jpg" if kind == "jpeg" else kind
    target = Path(out) if str(out or "").strip() else source.with_suffix(f".{ext}")
    try:
        if kind == "jpeg" and image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, "white")
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        image.save(str(target), kind.upper() if kind != "jpeg" else "JPEG")
    except Exception as exc:
        raise ImageOpError(f"转换失败：{exc}") from exc
    return _result(target, f"→ {ext}")


def run_op(source: Path, op: str, **kwargs) -> ImageResult:
    """统一入口：按 op 分发。"""
    name = str(op or "").strip().lower()
    if name not in _OPS:
        raise ImageOpError(f"不认识的操作：{name}（支持 {'、'.join(_OPS)}）")
    if name == "resize":
        return resize(source, width=kwargs.get("width", 0), height=kwargs.get("height", 0),
                      out=kwargs.get("out", ""))
    if name == "compress":
        return compress(source, quality=kwargs.get("quality", 70), out=kwargs.get("out", ""))
    if name == "watermark":
        return watermark(source, text=kwargs.get("text", ""), out=kwargs.get("out", ""),
                         position=kwargs.get("position", "br"))
    return convert(source, fmt=kwargs.get("fmt", "png"), out=kwargs.get("out", ""))
