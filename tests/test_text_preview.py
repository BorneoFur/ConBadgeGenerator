import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, ImageChops

from app import services
from app.main import template_text_preview
from font_fixtures import sample_font


class TextPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        data = patch.object(services, "DATA_ROOT", self.root)
        data.start()
        self.addCleanup(data.stop)
        fallback = self.root / "fallback.ttf"
        sample_font("Fallback", characters="".join(chr(i) for i in range(32, 127)) + "名字测试").save(fallback)
        fonts = patch("app.google_fonts.ensure_font", return_value={"path": fallback})
        fonts.start()
        self.addCleanup(fonts.stop)
        self.element = {"type": "text", "field": "display_name", "x_mm": 10, "y_mm": 12,
                        "width_mm": 60, "height_mm": 14, "font_size_pt": 30,
                        "min_font_size_pt": 9, "max_lines": 2, "color": "#123456"}

    def assert_matches_badge(self, element, value, fonts=None):
        saved = services.save_template_layout("attendees", [element], fonts)
        identifier = saved["id"]
        payload = {"element": element, "value": value}
        if fonts is not None:
            payload["fonts"] = fonts
        response = template_text_preview(identifier, payload)
        layer = Image.open(io.BytesIO(response.body)).convert("RGBA")
        # Records imported before a built-in template was edited must use the new layout.
        badge, printing = services.render_badge(self.root, {"template_id": "attendees", "tier": "Attendees", "display_name": value})
        ppi = printing["ppi"]
        x = services.mm_to_px(printing["bleed_mm"], ppi) + services.mm_to_px(element["x_mm"], ppi)
        y = services.mm_to_px(printing["bleed_mm"], ppi) + services.mm_to_px(element["y_mm"], ppi)
        expected = Image.new("RGBA", layer.size, "#1d2a44")
        expected.alpha_composite(layer)
        crop = badge.crop((x, y, x + layer.width, y + layer.height))
        self.assertIsNone(ImageChops.difference(crop, expected.convert("RGB")).getbbox())
        return layer

    def test_pixels_match_for_sizing_wrapping_and_alignment(self):
        for name in ("Name", "A long attendee name that must wrap and shrink", "名字测试"):
            for align in ("left", "center", "right"):
                with self.subTest(name=name, align=align):
                    element = {**self.element, "align": align, "vertical_align": "bottom",
                               "letter_spacing_pt": .5, "line_spacing_pt": 1}
                    self.assert_matches_badge(element, name)

    def test_font_styles_and_draft_preview_do_not_save(self):
        for style in ("regular", "bold"):
            font = sample_font("PreviewTest", style.title())
            if style == "bold":
                font["glyf"]["A"].coordinates[1] = (400, 700)
            stream = io.BytesIO()
            font.save(stream)
            result = services.add_template_font("tier-attendees" if style == "bold" else "attendees",
                                                "PreviewTest", style, "font.ttf", stream.getvalue())
        root = services.discover_templates()[result["id"]]["root"]
        layers = []
        for style in ("regular", "bold"):
            element = {**self.element, "font_family": result["font"]["id"], "font_style": style}
            # Save using the custom root which contains the uploaded assets.
            services.save_template_layout(result["id"], [element], result["fonts"])
            original = (root / "template.json").read_bytes()
            response = template_text_preview(result["id"], {"element": element, "fonts": result["fonts"], "value": "AAA"})
            layer = Image.open(io.BytesIO(response.body)).convert("RGBA")
            badge, _ = services.render_badge(self.root, {"template_id": result["id"], "tier": "Attendees", "display_name": "AAA"})
            expected = Image.new("RGBA", layer.size, "#1d2a44")
            expected.alpha_composite(layer)
            x = services.mm_to_px(3, 300) + services.mm_to_px(10, 300)
            y = services.mm_to_px(3, 300) + services.mm_to_px(12, 300)
            self.assertIsNone(ImageChops.difference(badge.crop((x, y, x + layer.width, y + layer.height)), expected.convert("RGB")).getbbox())
            draft = {**element, "font_size_pt": 15}
            template_text_preview(result["id"], {"element": draft, "fonts": result["fonts"], "value": "AAA"})
            self.assertEqual((root / "template.json").read_bytes(), original)
            layers.append(layer.tobytes())
        self.assertNotEqual(*layers)

    def test_invalid_settings_return_actionable_errors(self):
        for changes in ({"font_size_pt": 0}, {"width_mm": float("inf")},
                        {"font_language": "unknown"}, {"font_language": []},
                        {"font_asset": "../outside.ttf"}, {"type": "artwork"}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as error:
                template_text_preview("attendees", {"element": {**self.element, **changes}, "value": "Name"})
            self.assertEqual(error.exception.status_code, 400)

    def test_custom_text_roundtrip_and_deletion(self):
        element = {**self.element, "name": "Event title", "text": "Convention\n2026"}
        del element["field"]
        saved = services.save_template_layout("attendees", [element])
        services.import_template_set_json(services.template_set_json())
        restored = services.discover_templates()[saved["id"]]["manifest"]["elements"][0]
        self.assertEqual(restored["name"], "Event title")
        self.assertEqual(restored["text"], "Convention\n2026")
        record = {"template_id": saved["id"], "tier": "Attendees", "display_name": "Different attendee"}
        custom_badge, _ = services.render_badge(self.root, record)
        bound = {**element, "field": "display_name"}
        del bound["text"]
        services.save_template_layout(saved["id"], [bound])
        expected, _ = services.render_badge(self.root, {**record, "display_name": element["text"]})
        self.assertIsNone(ImageChops.difference(custom_badge, expected).getbbox())
        # Removing the final custom layer is a valid, persistable empty layout.
        services.save_template_layout(saved["id"], [])
        empty, _ = services.render_badge(self.root, record)
        self.assertIsNotNone(ImageChops.difference(custom_badge, empty).getbbox())

    def test_custom_text_validation(self):
        for content in (123, "x" * 8193):
            with self.subTest(content_type=type(content)), self.assertRaises(services.BadgeError):
                services.save_template_layout("attendees", [{**self.element, "text": content}])


if __name__ == "__main__":
    unittest.main()
