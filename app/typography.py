"""Shared Unicode layout: grapheme fallback, HarfBuzz shaping, and FreeType rasterization."""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

import freetype
import regex
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from PIL import Image, ImageColor

from app import google_fonts, security


@lru_cache(maxsize=24)
def _font_data(path: str, modified: int, size: int):
    with TTFont(path, lazy=True) as font:
        coverage = frozenset((font.getBestCmap() or {}).keys())
        axes = tuple((a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in font['fvar'].axes) if 'fvar' in font else ()
    return coverage, axes, hb.Face(Path(path).read_bytes())


def font_data(path: Path):
    stat = path.stat()
    return _font_data(str(path), stat.st_mtime_ns, stat.st_size)


def clear_font_cache() -> None:
    _font_data.cache_clear()


def graphemes(value: str) -> list[str]:
    # NFC composes accents and Hangul when possible; \X keeps remaining marks with their base.
    return regex.findall(r'\X', unicodedata.normalize('NFC', value))


def supports(path: Path, cluster: str) -> bool:
    coverage = font_data(path)[0]
    return all(ord(c) in coverage for c in cluster if not c.isspace() and unicodedata.category(c) != 'Cf' and not 0xFE00 <= ord(c) <= 0xFE0F)


class FontResolver:
    def __init__(self, primary: Path | None, cache: Path, value: str, language: str = 'auto'):
        self.primary, self.cache, self.paths = primary, cache, {}
        self.region = {'zh-Hans': 'notosanssc', 'zh-Hant': 'notosanstc', 'ja': 'notosansjp', 'ko': 'notosanskr'}.get(language)
        if not self.region:
            self.region = 'notosansjp' if regex.search(r'\p{Hiragana}|\p{Katakana}', value) else 'notosanskr' if regex.search(r'\p{Hangul}', value) else 'notosanssc'

    def choose(self, cluster: str) -> Path:
        if cluster in self.paths:
            return self.paths[cluster]
        if self.primary and supports(self.primary, cluster):
            self.paths[cluster] = self.primary
            return self.primary
        preferred = 'notosansjp' if regex.search(r'\p{Hiragana}|\p{Katakana}', cluster) else 'notosanskr' if regex.search(r'\p{Hangul}', cluster) else self.region if regex.search(r'\p{Han}', cluster) else 'notosans'
        for identifier in dict.fromkeys([preferred, 'notosans', self.region, 'notosanssc', 'notosanstc', 'notosansjp', 'notosanskr']):
            path = google_fonts.ensure_font(identifier, self.cache)['path']
            if supports(path, cluster):
                self.paths[cluster] = path
                return path
        codes = ' '.join(f'U+{ord(c):04X}' for c in cluster)
        raise google_fonts.FontError(f'No available font supports "{cluster}" ({codes}). Upload a font containing this character and select it for this layer.')


class SizedFont:
    def __init__(self, path: Path, size: int, style: str):
        _, axes, face = font_data(path)
        self.font = hb.Font(face)
        hb.ot_font_set_funcs(self.font)
        self.font.scale = (size * 64, size * 64)
        self.raster = freetype.Face(str(path))
        self.raster.set_pixel_sizes(0, size)
        variations = {tag: max(low, min(high, 700 if 'bold' in style else 400)) if tag == 'wght' else default for tag, low, default, high in axes}
        if variations:
            self.font.set_variations(variations)
            self.raster.set_var_design_coords(list(variations.values()))
        self.shapes = {}
        self.ascent = self.raster.size.ascender / 64
        self.descent = -self.raster.size.descender / 64

    def shape(self, text: str, tracking: float):
        if (text, tracking) in self.shapes:
            return self.shapes[text, tracking]
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(self.font, buffer, {'liga': False, 'clig': False} if tracking else {})
        glyphs, cursor = [], 0.0
        infos, positions = buffer.glyph_infos, buffer.glyph_positions
        for index, (info, position) in enumerate(zip(infos, positions)):
            glyphs.append((info.codepoint, cursor + position.x_offset / 64, -position.y_offset / 64))
            cursor += position.x_advance / 64
            if index + 1 < len(infos) and infos[index + 1].cluster != info.cluster:
                cursor += tracking
        if len(self.shapes) < 512:
            self.shapes[text, tracking] = glyphs, cursor
        return glyphs, cursor

    def draw(self, canvas: Image.Image, text: str, x: float, baseline: float, color: str, tracking: float):
        glyphs, advance = self.shape(text, tracking)
        rgb = ImageColor.getrgb(color)
        for glyph_id, offset_x, offset_y in glyphs:
            if glyph_id == 0:
                continue  # Missing visible glyphs were rejected by FontResolver before shaping.
            self.raster.load_glyph(glyph_id, freetype.FT_LOAD_RENDER)
            slot = self.raster.glyph
            bitmap = slot.bitmap
            if not bitmap.width or not bitmap.rows:
                continue
            security.check_dimensions(bitmap.width, bitmap.rows)
            mask = Image.frombytes('L', (bitmap.width, bitmap.rows), bytes(bitmap.buffer), 'raw', 'L', abs(bitmap.pitch), 1 if bitmap.pitch >= 0 else -1)
            ink = Image.new('RGBA', mask.size, rgb)
            if len(rgb) == 4 and rgb[3] != 255:
                mask = mask.point(lambda value: round(value * rgb[3] / 255))
            ink.putalpha(mask)
            canvas.alpha_composite(ink, (round(x + offset_x + slot.bitmap_left), round(baseline + offset_y - slot.bitmap_top)))
        return advance


class TextLayout:
    def __init__(self, clusters: list[str], resolver: FontResolver, size: int, style: str, tracking: float):
        self.tracking = tracking
        self.clusters = clusters
        self.paths = {cluster: resolver.choose(cluster) for cluster in set(clusters) if cluster not in ('\n', '\r\n')}
        self.fonts = {path: SizedFont(path, size, style) for path in set(self.paths.values())}
        self.ascent = max((font.ascent for font in self.fonts.values()), default=size)
        self.descent = max((font.descent for font in self.fonts.values()), default=0)

    def runs(self, clusters):
        runs = []
        for cluster in clusters:
            path = self.paths[cluster]
            if runs and runs[-1][0] == path:
                runs[-1] = (path, runs[-1][1] + cluster)
            else:
                runs.append((path, cluster))
        return runs

    def measure(self, clusters):
        runs = self.runs(clusters)
        return sum(self.fonts[path].shape(text, self.tracking)[1] for path, text in runs) + max(0, len(runs) - 1) * self.tracking

    def wrap(self, width):
        lines, current = [], []
        for cluster in self.clusters:
            if cluster in ('\n', '\r\n'):
                lines.append(current); current = []
                continue
            if current and self.measure(current + [cluster]) > width:
                # Prefer spaces as word boundaries, but allow long words/CJK to break by grapheme.
                spaces = [i for i, char in enumerate(current) if char.isspace()]
                if spaces and spaces[-1] > 0:
                    split = spaces[-1]
                    lines.append(current[:split]); current = current[split + 1:]
                else:
                    lines.append(current); current = []
                if current and self.measure(current + [cluster]) > width:
                    lines.append(current); current = []
            current.append(cluster)
        lines.append(current)
        return lines

    def draw_line(self, canvas, clusters, x, baseline, color):
        for path, text in self.runs(clusters):
            x += self.fonts[path].draw(canvas, text, x, baseline, color, self.tracking) + self.tracking


def render_text(element: dict, value: str, primary: Path | None, cache: Path) -> Image.Image:
    ppi = element['_ppi']
    security.finite_number(ppi, 1, 1200, 'PPI')
    security.validate_element_geometry(element, ppi)
    if len(value) > 8192:
        raise security.BadgeError('Text must contain at most 8192 characters.')
    width = max(1, round(element['width_mm'] / 25.4 * ppi))
    height = max(1, round(element['height_mm'] / 25.4 * ppi))
    image = Image.new('RGBA', (width, height))
    if not value:
        return image
    clusters = graphemes(value)
    resolver = FontResolver(primary, cache, value, element.get('font_language', 'auto'))
    tracking = float(element.get('letter_spacing_pt', 0)) / 72 * ppi
    spacing = float(element.get('line_spacing_pt', 0)) / 72 * ppi
    max_lines = max(1, int(element.get('max_lines', 99)))
    low = max(1, round(float(element.get('min_font_size_pt', 9)) / 72 * ppi))
    high = max(low, round(float(element.get('font_size_pt', 16)) / 72 * ppi))
    if high > 4096:
        raise security.BadgeError('The font size is too large to render.')
    minimum = low
    chosen = None
    # Binary search uses the same shaped measurements as drawing, including fallback fonts.
    while low <= high:
        size = (low + high) // 2
        layout = TextLayout(clusters, resolver, size, element.get('font_style', 'regular'), tracking)
        lines = layout.wrap(width)
        line_height = max(1, layout.ascent + layout.descent + spacing)
        fits = len(lines) <= max_lines and len(lines) * line_height <= height and all(layout.measure(line) <= width for line in lines)
        if fits or size == minimum:
            chosen = layout, lines[:max_lines], line_height
        if fits:
            low = size + 1
        else:
            high = size - 1
    if chosen is None:
        layout = TextLayout(clusters, resolver, minimum, element.get('font_style', 'regular'), tracking)
        chosen = layout, layout.wrap(width)[:max_lines], max(1, layout.ascent + layout.descent + spacing)
    layout, lines, line_height = chosen
    total = len(lines) * line_height
    vertical = element.get('vertical_align', 'middle')
    y = 0 if vertical == 'top' else max(0, height - total) if vertical == 'bottom' else max(0, (height - total) / 2)
    for line in lines:
        length = layout.measure(line)
        align = element.get('align', 'left')
        x = 0 if align == 'left' else (width - length) / 2 if align == 'center' else width - length
        layout.draw_line(image, line, x, y + layout.ascent, element.get('color', '#000000'))
        y += line_height
    return image
