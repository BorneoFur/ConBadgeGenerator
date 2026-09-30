"""Download unmodified OFL/Apache/UFL fonts from Google's repository, with their licenses."""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import uuid
from pathlib import Path
from urllib.error import HTTPError
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
BASE = "https://raw.githubusercontent.com/google/fonts/main/"
_LICENSES = {
    "OFL": {"id": "OFL-1.1", "filename": "OFL.txt", "markers": (b"SIL OPEN FONT LICENSE",)},
    "APACHE2": {"id": "Apache-2.0", "filename": "LICENSE.txt", "markers": (b"Apache License", b"Version 2.0")},
    "UFL": {"id": "Ubuntu-font-1.0", "filename": "UFL.txt", "markers": (b"UBUNTU FONT LICENCE", b"Version 1.0")},
}


class FontError(ValueError):
    pass


class _FontNotFoundError(FontError):
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
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            raise _FontNotFoundError("This Google font file was not found in the official repository.") from exc
        raise FontError("Could not download this Google font. Check the internet connection and retry. You can also upload a TTF/OTF file.") from exc
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
            if isinstance(info, dict) and path.is_file():
                license_spec = next((item for item in _LICENSES.values() if item["id"] == info.get("license")), None)
                if license_spec and (directory / license_spec["filename"]).is_file():
                    return {**info, "path": path, "license_path": directory / license_spec["filename"]}
        for repository in ("ofl", "apache", "ufl"):
            family_path = f"{repository}/{identifier}"
            try:
                metadata = download(f"{family_path}/METADATA.pb").decode("utf-8")
                break
            except _FontNotFoundError:
                continue
        else:
            raise FontError("This Google font was not found among supported OFL, Apache 2.0, or UFL families. Check the family link.")
        name_match = re.search(r'^name: "([^"]+)"', metadata, re.M)
        license_match = re.search(r'^license: "([^"]+)"', metadata, re.M)
        license_spec = _LICENSES.get(license_match[1]) if license_match else None
        if not name_match or not license_spec:
            raise FontError("Automatic downloads support Google Fonts families licensed under OFL, Apache 2.0, or UFL only.")
        blocks = re.findall(r'fonts\s*\{(.*?)\n\}', metadata, re.S)
        candidates = {"normal": [], "italic": []}
        for block in blocks:
            filename = re.search(r'filename: "([^"]+)"', block)
            style = re.search(r'style: "([^"]+)"', block)
            weight = re.search(r'weight: (\d+)', block)
            if filename and style and style[1] in candidates:
                candidates[style[1]].append((0 if "wght" in filename[1] else 1, abs(int(weight[1]) - (700 if bold else 400)) if weight else 0, filename[1]))
        selected_style = "italic" if italic else "normal"
        # Some families, such as Molle, have only an italic face even for their default design.
        if not italic and not bold and not candidates["normal"]:
            selected_style = "italic"
        if not candidates[selected_style]:
            raise FontError("This Google font does not provide the requested style.")
        filename = min(candidates[selected_style])[2]
        applied_style = ("bold_italic" if bold else "italic") if selected_style == "italic" else ("bold" if bold else "regular")
        if not re.fullmatch(r"[A-Za-z0-9_.\[\],-]+\.ttf", filename):
            raise FontError("This Google font does not provide a supported TTF file.")
        content = download(f"{family_path}/{filename}")
        license_content = download(f"{family_path}/{license_spec['filename']}")
        if not all(marker in license_content for marker in license_spec["markers"]):
            raise FontError("The Google font's license could not be verified.")
        try:
            with TTFont(io.BytesIO(content)) as font:
                if not font.getBestCmap():
                    raise FontError("This font has no supported Unicode character map.")
        except Exception as exc:
            raise FontError("The downloaded font could not be read. Please try another font.") from exc
        info = {"id": identifier, "name": name_match[1], "source": "google-fonts", "license": license_spec["id"], "style": applied_style,
                "url": "https://fonts.google.com/specimen/" + quote(name_match[1].replace(" ", "+"), safe="+"),
                "download_url": BASE + quote(f"{family_path}/{filename}"), "sha256": hashlib.sha256(content).hexdigest()}
        _write(directory / f"{key}.ttf", content)
        _write(directory / license_spec["filename"], license_content)
        _write(index, json.dumps(info).encode())
        return {**info, "path": directory / f"{key}.ttf", "license_path": directory / license_spec["filename"]}
