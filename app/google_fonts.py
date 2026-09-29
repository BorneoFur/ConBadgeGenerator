"""Download unmodified OFL fonts from Google's repository, with their licenses."""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from fontTools.ttLib import TTFont

CATALOG = [
    {"id": "notosans", "name": "Noto Sans", "languages": "Latin, accented letters"},
    {"id": "notosanssc", "name": "Noto Sans SC", "languages": "Simplified Chinese"},
    {"id": "notosanstc", "name": "Noto Sans TC", "languages": "Traditional Chinese"},
    {"id": "notosansjp", "name": "Noto Sans JP", "languages": "Japanese"},
    {"id": "notosanskr", "name": "Noto Sans KR", "languages": "Korean"},
]
_LOCK = threading.RLock()
BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"


class FontError(ValueError):
    pass


def family_id(value: str) -> str:
    """Accept a catalog ID or a Google Fonts specimen link, never an arbitrary URL."""
    value = value.strip()
    if "://" in value:
        url = urlparse(value)
        if url.scheme != "https" or url.netloc != "fonts.google.com" or not url.path.startswith("/specimen/"):
            raise FontError("Use a Google Fonts family link such as https://fonts.google.com/specimen/Noto+Sans.")
        value = unquote(url.path[len("/specimen/"):]).replace("+", " ")
    identifier = re.sub(r"[ -]", "", value).lower()
    if not re.fullmatch(r"[a-z0-9]{1,80}", identifier):
        raise FontError("Choose a font or paste its Google Fonts specimen link.")
    return identifier


def download(relative: str) -> bytes:
    try:
        request = Request(BASE + quote(relative, safe="/"), headers={"User-Agent": "ConBadgeGenerator/1.0"})
        with urlopen(request, timeout=45) as response:
            content = response.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise FontError("This font file exceeds the 64 MB download limit.")
        return content
    except (OSError, ValueError) as exc:
        raise FontError("Could not download this Google font. Check the internet connection and family link, then retry. You can also upload a TTF/OTF file.") from exc


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_font(value: str, cache: Path, italic: bool = False, bold: bool = False) -> dict:
    identifier = family_id(value)
    key = ("bold-" if bold else "") + ("italic" if italic else "regular")
    directory = cache / identifier
    with _LOCK:
        index = directory / f"{key}.json"
        if index.is_file():
            try:
                info = json.loads(index.read_text())
            except (ValueError, OSError):
                info = None  # A damaged cache entry can be downloaded again.
            path = directory / f"{key}.ttf"
            if isinstance(info, dict) and path.is_file() and (directory / "OFL.txt").is_file():
                return {**info, "path": path, "license_path": directory / "OFL.txt"}
        metadata = download(f"{identifier}/METADATA.pb").decode("utf-8")
        name_match = re.search(r'^name: "([^"]+)"', metadata, re.M)
        if not name_match or not re.search(r'^license: "OFL"', metadata, re.M):
            raise FontError("Automatic downloads support OFL-licensed Google Fonts families only.")
        blocks = re.findall(r'fonts\s*\{(.*?)\n\}', metadata, re.S)
        candidates = []
        for block in blocks:
            filename = re.search(r'filename: "([^"]+)"', block)
            style = re.search(r'style: "([^"]+)"', block)
            weight = re.search(r'weight: (\d+)', block)
            if filename and style and style[1] == ("italic" if italic else "normal"):
                candidates.append((0 if "wght" in filename[1] else 1, abs(int(weight[1]) - (700 if bold else 400)) if weight else 0, filename[1]))
        if not candidates:
            raise FontError("This Google font does not provide the requested style.")
        filename = min(candidates)[2]
        if not re.fullmatch(r"[A-Za-z0-9_.\[\],-]+\.ttf", filename):
            raise FontError("This Google font does not provide a supported TTF file.")
        content = download(f"{identifier}/{filename}")
        license_content = download(f"{identifier}/OFL.txt")
        if b"SIL OPEN FONT LICENSE" not in license_content:
            raise FontError("The Google font's OFL license could not be verified.")
        try:
            with TTFont(io.BytesIO(content)) as font:
                if not font.getBestCmap():
                    raise FontError("This font has no supported Unicode character map.")
        except Exception as exc:
            raise FontError("The downloaded font could not be read. Please try another font.") from exc
        info = {"id": identifier, "name": name_match[1], "source": "google-fonts", "license": "OFL-1.1",
                "url": "https://fonts.google.com/specimen/" + quote(name_match[1].replace(" ", "+"), safe="+"),
                "download_url": BASE + quote(f"{identifier}/{filename}"), "sha256": hashlib.sha256(content).hexdigest()}
        _write(directory / f"{key}.ttf", content)
        _write(directory / "OFL.txt", license_content)
        _write(index, json.dumps(info).encode())
        return {**info, "path": directory / f"{key}.ttf", "license_path": directory / "OFL.txt"}
