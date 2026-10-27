"""打包与解包：用标准库补"没有压缩软件"的缺口。

解包是攻击面最大的动作（zip slip / 压缩炸弹），所以这里的约束是硬性的：
- 条目路径规范化后必须落在目标目录内（拒绝绝对路径与 `..`）；
- 条目数量、单个与总体积、压缩比都有上限；
- 只解文件，不跟随符号链接。
"""

from __future__ import annotations

import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_FILES = 500
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_RATIO = 200  # 单条目解压后 / 压缩后 的上限，防压缩炸弹


class ArchiveError(Exception):
    """打包/解包失败（信息面向模型可读）。"""


@dataclass(slots=True)
class ArchiveResult:
    path: Path
    entries: int
    bytes: int
    note: str = ""


def pack(files: list[Path], out: Path, *, base: Path) -> ArchiveResult:
    """把一批文件打包成 zip（保留相对 base 的目录结构）。"""
    items = [p for p in files if p and Path(p).is_file()]
    if not items:
        raise ArchiveError("没有可打包的文件")
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for path in items:
                path = Path(path)
                try:
                    arcname = path.resolve().relative_to(Path(base).resolve()).as_posix()
                except ValueError:
                    arcname = path.name
                zf.write(str(path), arcname)
                total += path.stat().st_size
    except Exception as exc:
        raise ArchiveError(f"打包失败：{type(exc).__name__}: {str(exc)[:120]}") from exc
    return ArchiveResult(path=out, entries=len(items), bytes=out.stat().st_size,
                         note=f"原始 {total // 1024}KB → 压缩包 {out.stat().st_size // 1024}KB")


def _safe_member(name: str, dest: Path) -> Path:
    """把条目名映射到目标路径，越界就拒绝。"""
    raw = str(name or "").replace("\\", "/").lstrip("/")
    if not raw or raw.endswith("/"):
        raise ArchiveError(f"条目名非法：{name!r}")
    if ".." in raw.split("/"):
        raise ArchiveError(f"条目里含 `..`，已拒绝：{name!r}")
    target = (dest / raw).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError as exc:
        raise ArchiveError(f"条目越出目标目录，已拒绝：{name!r}") from exc
    return target


def unpack(archive: Path, dest: Path, *, max_files: int = MAX_FILES,
           max_total_bytes: int = MAX_TOTAL_BYTES) -> ArchiveResult:
    """解包 zip/tar（只解文件，带体积与条目数限制）。"""
    archive = Path(archive)
    if not archive.is_file():
        raise ArchiveError(f"找不到压缩包：{archive.name}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    written: list[str] = []

    def _guard(size: int) -> None:
        nonlocal total
        total += int(size or 0)
        if count > max_files:
            raise ArchiveError(f"条目数超过上限 {max_files}")
        if total > max_total_bytes:
            raise ArchiveError(f"解压后总量超过上限 {max_total_bytes // 1024 // 1024}MB")

    try:
        if zipfile.is_zipfile(str(archive)):
            with zipfile.ZipFile(str(archive)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    count += 1
                    _guard(info.file_size)
                    if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
                        raise ArchiveError(f"压缩比异常（可能是压缩炸弹）：{info.filename}")
                    target = _safe_member(info.filename, dest)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as fh:
                        fh.write(src.read())
                    written.append(info.filename)
        elif tarfile.is_tarfile(str(archive)):
            with tarfile.open(str(archive)) as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    count += 1
                    _guard(member.size)
                    target = _safe_member(member.name, dest)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, open(target, "wb") as fh:
                        fh.write(src.read())
                    written.append(member.name)
        else:
            raise ArchiveError("不认识的压缩包格式（只支持 zip / tar / tar.gz / tgz）")
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"解包失败：{type(exc).__name__}: {str(exc)[:120]}") from exc

    return ArchiveResult(path=dest, entries=len(written), bytes=total,
                         note="、".join(written[:5]) + ("…" if len(written) > 5 else ""))
