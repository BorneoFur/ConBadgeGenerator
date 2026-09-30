"""Limits and validation shared by uploads, saved assets, and rendering."""
from __future__ import annotations

import io
import math
import re
import warnings
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from PIL import Image, ImageFont, ImageOps, UnidentifiedImageError

MIB = 1024 * 1024
GIB = 1024 * MIB
MAX_IMAGE_BYTES = 32 * MIB
MAX_BACKGROUND_BYTES = 2 * GIB
MAX_STORED_IMAGE_BYTES = MAX_BACKGROUND_BYTES
MAX_IMAGE_PIXELS = 25_000_000
MAX_BACKGROUND_PIXELS = 100_000_000
MAX_RENDER_PIXELS = 100_000_000
MAX_LAYER_PIXELS = 400_000_000
MAX_SIDE = 32_768
MAX_PREVIEW_SIDE = 2048
MAX_FONT_BYTES = 128 * MIB
MAX_PROFILE_BYTES = 256 * MIB
MAX_SPREADSHEET_BYTES = 64 * MIB
MAX_TEMPLATE_BYTES = 128 * MIB
MAX_REQUEST_BYTES = 4 * GIB
MAX_ASSETS = 1000
MAX_RECORDS = 10_000
MAX_LAYERS = 100
IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
ASSET_EXTENSIONS = set(IMAGE_EXTENSIONS) | {".ttf", ".otf", ".txt"}

ImageSource = bytes | Path | BinaryIO
# Keep Pillow's bomb protection enabled at the larger background limit.
Image.MAX_IMAGE_PIXELS = MAX_BACKGROUND_PIXELS
_IMAGE_LOCK = threading.RLock()


def image_task(function):
    """Serialize pixel-heavy operations within this local server process."""
    @wraps(function)
    def run(*args, **kwargs):
        with _IMAGE_LOCK:
            return function(*args, **kwargs)
    return run


class BadgeError(Exception):
    """An error that is safe to show in the local UI."""


def check_size(content: bytes, limit: int, label: str) -> None:
    if len(content) > limit:
        raise BadgeError(f"{label} exceeds the {limit // MIB} MiB limit.")


def source_size(source: ImageSource) -> int:
    if isinstance(source, bytes):
        return len(source)
    if isinstance(source, Path):
        return source.stat().st_size
    position = source.tell()
    try:
        source.seek(0, 2)
        return source.tell()
    finally:
        source.seek(position)


@contextmanager
def image_stream(source: ImageSource):
    if isinstance(source, bytes):
        with io.BytesIO(source) as stream:
            yield stream
    elif isinstance(source, Path):
        with source.open('rb') as stream:
            yield stream
    else:
        position = source.tell()
        try:
            source.seek(0)
            yield source
        finally:
            source.seek(position)


def safe_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise BadgeError("Asset paths must be relative paths inside their data folder.")
    text = value.replace("\\", "/")
    path = PurePosixPath(text)
    if (path.is_absolute() or PureWindowsPath(text).drive
            or any(part in ("", ".", "..") for part in text.split("/"))
            or any(ord(char) < 32 or char == ":" for char in text)):
        raise BadgeError("Asset paths must be relative paths inside their data folder.")
    return Path(*path.parts)


def asset_path(root: Path, value: str) -> Path:
    path = root / safe_relative_path(value)
    try:
        contained = path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError) as exc:
        raise BadgeError("The asset path could not be resolved safely.") from exc
    if not contained:
        raise BadgeError("Asset paths must stay inside their data folder.")
    return path


def validate_font_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise BadgeError("Font IDs must contain only letters, numbers, underscores, or hyphens.")


def finite_number(value: object, low: float, high: float, label: str) -> float:
    if (type(value) not in (int, float) or not low <= value <= high
            or not math.isfinite(value)):
        raise BadgeError(f"Invalid {label}: expected a finite number from {low} to {high}.")
    return value


def check_dimensions(width: int, height: int, *, limit: int = MAX_RENDER_PIXELS) -> None:
    if width < 1 or height < 1 or width > MAX_SIDE or height > MAX_SIDE or width * height > limit:
        raise BadgeError(f"Image area is too large; use at most {limit:,} pixels and {MAX_SIDE:,} pixels per side.")


def validate_element_geometry(element: dict, ppi: int) -> int:
    for key in ("width_mm", "height_mm"):
        finite_number(element.get(key), .001, 2000, key)
    for key in ("x_mm", "y_mm"):
        finite_number(element.get(key, 0), -2000, 2000, key)
    limits = {"rotation_degrees": (-36000, 36000), "scale": (.01, 20),
              "offset_x_pct": (-1000, 1000), "offset_y_pct": (-1000, 1000),
              "font_size_pt": (1, 1000), "min_font_size_pt": (1, 1000),
              "letter_spacing_pt": (-1000, 1000), "line_spacing_pt": (-1000, 1000),
              "max_lines": (1, 1000)}
    for key, (low, high) in limits.items():
        if key in element:
            finite_number(element[key], low, high, key)
    width = max(1, round(element["width_mm"] / 25.4 * ppi))
    height = max(1, round(element["height_mm"] / 25.4 * ppi))
    check_dimensions(width, height)
    return width * height


def _ppi(info: dict) -> int:
    dpi = info.get("dpi")
    value = dpi[0] if isinstance(dpi, (list, tuple)) and dpi else 300
    return round(value) if type(value) in (int, float) and 72 <= value <= 1200 and math.isfinite(value) else 300


@image_task
def decode_image(source: ImageSource, filename: str | None = None, *, max_pixels: int | None = None) -> tuple[Image.Image, int]:
    """Decode only PNG/JPEG from a seekable source, without reading the entire file.

    Dimensions are checked before loading pixels, and returned pixels have no
    metadata. This is validation, not a decoder sandbox.
    """
    if isinstance(source, Path):
        filename = filename or source.name
    if source_size(source) > MAX_STORED_IMAGE_BYTES:
        raise BadgeError("Image exceeds the 2 GiB file limit.")
    max_pixels = MAX_BACKGROUND_PIXELS if max_pixels is None else max_pixels
    expected = IMAGE_EXTENSIONS.get(Path(filename or "image.png").suffix.lower())
    if expected is None:
        raise BadgeError("Choose a PNG or JPEG image.")
    try:
        with image_stream(source) as stream, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(stream, formats=("PNG", "JPEG")) as image:
                if image.format != expected:
                    raise BadgeError("The image content does not match its PNG/JPEG filename extension.")
                check_dimensions(*image.size, limit=max_pixels)
                if getattr(image, "n_frames", 1) != 1:
                    raise BadgeError("Animated images are not supported. Use a single PNG or JPEG image.")
                image.verify()
            stream.seek(0)
            with Image.open(stream, formats=("PNG", "JPEG")) as image:
                image.load()
                ppi = _ppi(image.info)
                ImageOps.exif_transpose(image, in_place=True)
                mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                clean = image.convert(mode)
                clean.info.clear()
                clean.getexif().clear()
                return clean, ppi
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, OverflowError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise BadgeError("The image is invalid, damaged, or too large. Choose a valid PNG or JPEG.") from exc


@image_task
def normalize_image(filename: str, content: bytes, *, stored: bool = False) -> tuple[bytes, int, int, int]:
    check_size(content, MAX_STORED_IMAGE_BYTES if stored else MAX_IMAGE_BYTES, "Image")
    image, ppi = decode_image(content, filename, max_pixels=MAX_BACKGROUND_PIXELS if stored else MAX_IMAGE_PIXELS)
    try:
        output = io.BytesIO()
        kind = IMAGE_EXTENSIONS[Path(filename).suffix.lower()]
        options = {"quality": 95, "subsampling": 0} if kind == "JPEG" else {}
        image.save(output, kind, dpi=(ppi, ppi), **options)
        data = output.getvalue()
        check_size(data, MAX_STORED_IMAGE_BYTES, "Decoded image")
        return data, image.width, image.height, ppi
    finally:
        image.close()


@image_task
def normalize_image_file(filename: str, source: ImageSource, destination: Path, *, background: bool = False) -> tuple[int, int, int]:
    """Encode uploads straight into staging; avoid copies of multi-gigabyte files."""
    limit = MAX_BACKGROUND_BYTES if background else MAX_IMAGE_BYTES
    if source_size(source) > limit:
        raise BadgeError(f"Image exceeds the {limit // MIB} MiB file limit.")
    image, ppi = decode_image(source, filename, max_pixels=MAX_BACKGROUND_PIXELS if background else MAX_IMAGE_PIXELS)
    try:
        kind = IMAGE_EXTENSIONS[Path(filename).suffix.lower()]
        options = {"quality": 95, "subsampling": 0} if kind == "JPEG" else {}
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, kind, dpi=(ppi, ppi), **options)
        if destination.stat().st_size > MAX_STORED_IMAGE_BYTES:
            raise BadgeError("Normalized image exceeds the 2 GiB file limit.")
        return image.width, image.height, ppi
    finally:
        image.close()


@image_task
def image_preview(path: Path, *, max_pixels: int | None = None) -> bytes:
    image, _ = decode_image(path, max_pixels=max_pixels)
    try:
        image.thumbnail((MAX_PREVIEW_SIDE, MAX_PREVIEW_SIDE), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()
    finally:
        image.close()


def validate_font(filename: str, content: bytes) -> None:
    check_size(content, MAX_FONT_BYTES, "Font")
    if Path(filename).suffix.lower() not in {".ttf", ".otf"} or content[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise BadgeError("Choose a valid TTF or OTF font.")
    try:
        ImageFont.truetype(io.BytesIO(content), size=12)
    except (OSError, ValueError) as exc:
        raise BadgeError("The uploaded font file could not be read.") from exc


def normalize_asset(filename: str, content: bytes) -> bytes:
    extension = Path(filename).suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return normalize_image(filename, content, stored=True)[0]
    if extension in {".ttf", ".otf"}:
        validate_font(filename, content)
    elif extension == ".txt":
        check_size(content, MIB, "License text")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BadgeError("License text must be UTF-8.") from exc
    else:
        raise BadgeError("Template assets must be PNG/JPEG images, TTF/OTF fonts, or TXT licenses.")
    return content
