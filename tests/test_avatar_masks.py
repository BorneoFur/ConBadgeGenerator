import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import services


class AvatarMaskTests(unittest.TestCase):
    def test_editor_mask_alpha_matches_rendered_avatar(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(services, "DATA_ROOT", Path(temporary)):
            root = Path(temporary)
            (root / "assets").mkdir()
            Image.new("RGBA", (3, 1), "red").save(root / "assets/avatar.png")
            gray = Image.new("L", (3, 1))
            gray.putdata([0, 128, 255])
            rgba = Image.new("RGBA", (3, 1), "black")
            rgba.putalpha(gray)
            palette = Image.new("P", (3, 1))
            palette.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
            palette.putdata([0, 1, 1])
            palette.info["transparency"] = 0
            for mask in (gray, rgba, palette):
                with self.subTest(mode=mask.mode):
                    content = io.BytesIO()
                    mask.save(content, "PNG")
                    result = services.add_template_asset("sponsors", "mask", "mask.png", content.getvalue())
                    template = services.discover_templates()[result["id"]]
                    preview = Image.open(io.BytesIO(services.template_mask_preview(result["id"], result["asset"])))
                    element = {"type": "masked-image", "field": "avatar_asset", "x_mm": 0, "y_mm": 0,
                               "width_mm": 3, "height_mm": 1, "mask_asset": result["asset"]}
                    # At 25.4 PPI, each millimetre maps to one pixel.
                    canvas = Image.new("RGBA", (5, 3))
                    services._draw_avatar(canvas, element, {"avatar_asset": "avatar.png"}, root, template["root"], 0, 25.4)
                    self.assertEqual(list(preview.getchannel("A").getdata()),
                                     list(canvas.crop((1, 1, 4, 2)).getchannel("A").getdata()))

    def test_removal_can_be_saved_and_mask_asset_retained_for_undo(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(services, "DATA_ROOT", Path(temporary)):
            content = io.BytesIO()
            Image.new("L", (10, 10), 128).save(content, "PNG")
            result = services.add_template_asset("sponsors", "mask", "mask.png", content.getvalue())
            template = services.discover_templates()[result["id"]]
            elements = template["manifest"]["elements"]
            elements[0]["mask_asset"] = result["asset"]
            services.save_template_layout(result["id"], elements)
            del elements[0]["mask_asset"]
            services.save_template_layout(result["id"], elements)
            saved = services.discover_templates()[result["id"]]
            self.assertNotIn("mask_asset", saved["manifest"]["elements"][0])
            self.assertTrue((saved["root"] / result["asset"]).is_file())


if __name__ == "__main__":
    unittest.main()
