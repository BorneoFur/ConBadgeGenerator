import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import zxingcpp

from app import services


class BarcodeTests(unittest.TestCase):
    def assert_decodes(self, image, value, kind):
        result = zxingcpp.read_barcode(image)
        self.assertIsNotNone(result)
        self.assertEqual(result.bytes, value.encode("utf-8"))
        self.assertEqual(result.format, {"qr": zxingcpp.BarcodeFormat.QRCode,
                         "data-matrix": zxingcpp.BarcodeFormat.DataMatrix,
                         "aztec": zxingcpp.BarcodeFormat.Aztec}[kind])

    def test_preview_characters_and_formats(self):
        for kind in ("qr", "data-matrix", "aztec"):
            for value in ("Ticket-A001", "".join(map(chr, range(128))), "你好 · Badge 🐺"):
                with self.subTest(kind=kind, value=value):
                    self.assert_decodes(Image.open(io.BytesIO(services.qr_preview(value, symbology=kind))), value, kind)

    def test_legacy_qr_default(self):
        self.assert_decodes(Image.open(io.BytesIO(services.qr_preview("legacy"))), "legacy", "qr")

    def test_saved_template_roundtrip_and_master_render(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(services, "DATA_ROOT", Path(temporary)):
            for kind in ("qr", "data-matrix", "aztec"):
                with self.subTest(kind=kind):
                    manifest = copy.deepcopy(services.discover_templates()["attendees"]["manifest"])
                    element = {"type": "qr", "symbology": kind, "field": "qr_token", "x_mm": 10,
                               "y_mm": 10, "width_mm": 60, "height_mm": 40}
                    saved = services.save_template_layout("attendees", [element])
                    package = services.template_set_json()
                    services.import_template_set_json(package)
                    installed = services.discover_templates()
                    self.assertEqual(installed[saved["id"]]["manifest"]["elements"][0]["symbology"], kind)
                    value = "  Ticket-你好\x00\x1d\n  "
                    stream = io.StringIO()
                    writer = csv.writer(stream)
                    writer.writerow(["ticket_id", "display_name", "tier", "qr_token"])
                    writer.writerow(["A001", "Example", "Attendees", value])
                    summary = services.create_event(stream.getvalue().encode(), [])
                    event_dir, event = services.load_event(summary["id"])
                    self.assertEqual(event["records"][0]["qr_token"], value)
                    master, _ = services.render_badge(event_dir, event["records"][0])
                    self.assert_decodes(master, value, kind)

    def test_errors(self):
        for kind in ("qr", "data-matrix", "aztec"):
            with self.subTest(kind=kind):
                with self.assertRaises(services.BadgeError):
                    services.qr_preview("x" * 10000, symbology=kind)
                with self.assertRaises(services.BadgeError):
                    services._draw_qr(Image.new("RGBA", (100, 100)),
                        {"symbology": kind, "width_mm": .1, "height_mm": .1}, "test", 0, 300)
        with self.assertRaises(services.BadgeError):
            services.qr_preview("test", symbology="unsupported")
        with self.assertRaises(services.BadgeError):
            services.qr_preview("test", foreground="invalid")
        with self.assertRaises(services.BadgeError):
            services.qr_preview("")
        with tempfile.TemporaryDirectory() as temporary:
            manifest = copy.deepcopy(services.discover_templates()["attendees"]["manifest"])
            manifest["elements"][-1]["symbology"] = "unsupported"
            with self.assertRaises(services.BadgeError):
                services.validate_template(manifest, Path(temporary))


if __name__ == "__main__":
    unittest.main()
