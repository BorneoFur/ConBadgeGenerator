# Template manifest reference

A saved format is one self-contained JSON document containing every active Tier and its assets.
After loading, each Tier is restored to its own local folder. Asset paths are relative to
`template.json`; absolute paths and `..` are rejected.

```text
conbadge-template-formats.json
  templates[].manifest
  templates[].assets[].base64_data

Restored local Tier folder:
  template.json
  artwork/background.png
  masks/avatar-window.png
  fonts/NotoSans-Regular.ttf
```

## Minimal manifest

```json
{
  "id": "bah-2026-sponsors",
  "name": "Sponsors",
  "version": "1.0.0",
  "priority": 0,
  "profile_picture": true,
  "print": {
    "width_mm": 90,
    "height_mm": 125,
    "bleed_mm": 3,
    "safe_mm": 5,
    "ppi": 300
  },
  "background": {"asset": "artwork/background.png"},
  "elements": []
}
```

`width_mm` and `height_mm` are the trim size. The renderer adds `bleed_mm` on all four sides to
the master canvas. Element coordinates are relative to the trim area, so the template designer
does not have to offset every element for bleed.

The Template `name` directly matches an attendee's `tier`. The bundled example templates have
lower priority than locally edited Tiers. Loading a format creates or replaces only the Tiers
included in that JSON document; unrelated local Tiers are retained.

Custom copies of a bundled starter carry an optional `builtin_source` string, such as
`"attendees"`. It records the starter's ID so a renamed copy continues to replace that
starter in the Tier list and format exports. This field is retained by layout/asset
edits and formats JSON import/export. Deleting the custom copy makes its starter
available again. It is metadata, not a filesystem path.

## Elements

All `x_mm`, `y_mm`, `width_mm`, and `height_mm` values use millimetres.

### Text

```json
{
  "type": "text",
  "field": "display_name",
  "x_mm": 10,
  "y_mm": 72,
  "width_mm": 70,
  "height_mm": 16,
  "font_asset": "fonts/NotoSans-Regular.ttf",
  "font_family": "font-noto-sans",
  "font_style": "bold",
  "font_language": "zh-Hant",
  "font_size_pt": 24,
  "min_font_size_pt": 11,
  "max_lines": 2,
  "align": "center",
  "vertical_align": "middle",
  "letter_spacing_pt": 0,
  "line_spacing_pt": 0,
  "rotation_degrees": 0,
  "color": "#FFFFFF"
}
```

Supported fields are `ticket_id`, `display_name`, `tier`, `qr_token`, and `avatar_asset`. A text
field is automatically reduced down to `min_font_size_pt`, then wraps up to `max_lines`. Upload
TTF/OTF assets through the editor for reproducible multilingual output. `font_asset` remains
supported for older templates; the editor normally uses an uploaded font family and style.

### Custom text and layer names

A text element may use `text` instead of `field` to print the same literal content
on every badge in its Tier. Explicit line breaks are preserved. For example:

```json
{
  "type": "text",
  "name": "Event title",
  "text": "Convention\n2026",
  "x_mm": 10,
  "y_mm": 15,
  "width_mm": 70,
  "height_mm": 20,
  "font_size_pt": 24,
  "min_font_size_pt": 9,
  "max_lines": 4,
  "color": "#FFFFFF"
}
```

`name` labels the layer in the editor and does not change the printed content.
Custom text supports the same typography and transforms as attendee text.
Artwork layers can also have a `name`. The editor offers confirmed deletion for
artwork and custom text layers; Undo restores the removed layer while editing.
Deleting a layer retains its asset file so Undo can restore it.

### Avatar with arbitrary mask

```json
{
  "type": "masked-image",
  "field": "avatar_asset",
  "x_mm": 21,
  "y_mm": 12,
  "width_mm": 48,
  "height_mm": 48,
  "fit": "cover",
  "scale": 1,
  "offset_x_pct": 0,
  "offset_y_pct": 0,
  "rotation_degrees": 0,
  "mask_asset": "masks/avatar-window.png",
  "missing": "transparent"
}
```

The mask is a normal grayscale/alpha image: white is visible, black is hidden, and gray is
partially transparent. It can therefore be circular, polygonal, or an arbitrary illustration.
With `missing: "transparent"`, a missing avatar displays only the background.

`avatar_asset` is supplied automatically by the import process, not CSV: the selected avatar
folder is searched for an image whose filename stem equals `ticket_id`, with PNG, JPG, and JPEG
all accepted. A missing matching image leaves the slot transparent.

The first renderer implements centered `cover` crop. Per-attendee crop overrides are intentionally
not part of this initial manifest format.

### 2D barcode

```json
{
  "type": "qr",
  "symbology": "qr",
  "field": "qr_token",
  "x_mm": 65,
  "y_mm": 104,
  "width_mm": 16,
  "height_mm": 16,
  "quiet_zone_modules": 4,
  "foreground_color": "#000000",
  "background_color": "#FFFFFF",
  "rotation_degrees": 0,
  "error_correction": "M"
}
```

The `qr` element type is retained for compatibility. Its optional `symbology` is `qr` (default),
`data-matrix` (ECC 200), or `aztec`. Existing templates without this property remain QR Code.
All types encode the supplied value verbatim, including ASCII control characters and Unicode;
they do not create or validate ticket tokens. The CSV field remains `qr_token` for all three types.
Valid QR error correction values are `L`, `M`, `Q`, and `H`; Data Matrix and Aztec use automatic defaults.
Quiet zones are clamped to 4–16 modules for every type. The barcode fits inside its element with
its original aspect ratio, centered and scaled by a whole number of pixels per module. An area too
small to hold even one pixel per module is rejected. Longer content may require a larger print area.

### Artwork layer

```json
{
  "type": "artwork",
  "asset": "artwork/frame.png",
  "x_mm": 0,
  "y_mm": 0,
  "width_mm": 90,
  "height_mm": 125,
  "rotation_degrees": 0,
  "visible": true
}
```

Artwork layers are rendered in their `elements` order. Transparent PNG images can therefore sit
behind or in front of Avatar, text, and QR layers.

## Current boundary

The current renderer supports RGB backgrounds, artwork layers, text, masked images, and QR. A completely new
rendering primitive or a printer-specific PDF/X workflow requires renderer work; yearly artwork,
coordinates, fonts, masks, Tier names, and physical dimensions do not.

Background artwork may be JPG, JPEG, or PNG. Use PNG where artwork needs transparency; Avatar
masks should normally be PNG so their alpha channel is preserved.

### Font fallback and licenses

`font_language` accepts `auto` (default), `zh-Hans`, `zh-Hant`, `ja`, or `ko`.
It controls regional Han forms for automatic Noto fallback; an explicitly selected
font takes priority whenever it contains the requested grapheme. Missing characters
use cached Google fonts, downloading them on first use. Unsupported characters raise
an error. Text is NFC-normalized and shaped as runs, preserving combining marks.

Each font family may include `origins`, keyed by style. Google origins include
`source: "google-fonts"`, `license` (`"OFL-1.1"`, `"Apache-2.0"`, or `"Ubuntu-font-1.0"`),
`url`, `download_url`, `sha256`, optional `style` (the applied style), and
`license_asset`. Upload origins use `source: "upload"`. Template JSON exports retain
all font/license assets; users are responsible for permission to redistribute uploads.
