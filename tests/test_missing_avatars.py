"""Missing avatars are actionable warnings for avatar-enabled Tiers."""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile

from app import main, services
from test_upload_security import asgi_request, image_bytes


CSV = (b"ticket_id,display_name,tier,qr_token\n"
       b"A-1,No avatar needed,Attendees,a\n"
       b"S-1,Alice,Sponsors,b\n"
       b"SS-1,Bob,Super Sponsors,c\n")


class MissingAvatarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        data = patch.object(services, "DATA_ROOT", self.root)
        data.start()
        self.addCleanup(data.stop)

    def summary(self, event_id):
        status, headers, body = asyncio.run(
            asgi_request(f"/api/events/{event_id}/summary", method="GET"))
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        return json.loads(body)

    def missing_ids(self, result):
        return [badge["ticket_id"] for badge in result["missing_avatars"]]

    def test_csv_and_xlsx_without_avatar_folder_warn_only_for_enabled_tiers(self):
        for extension in ("csv", "xlsx"):
            with self.subTest(extension=extension):
                content = Path(f"app/static/example-attendees.{extension}").read_bytes()
                result = asyncio.run(main.import_event(
                    UploadFile(filename=f"attendees.{extension}", file=io.BytesIO(content)), []))
                self.assertEqual(result["record_count"], 3)
                self.assertEqual(result["missing_avatars"], [
                    {"ticket_id": "S-002", "display_name": "Alice", "tier": "Sponsors"},
                    {"ticket_id": "SS-003", "display_name": "Charlie", "tier": "Super Sponsors"},
                ])
                self.assertEqual(self.summary(result["id"]), result)

    def test_partial_match_and_reimport_with_png_jpg_jpeg(self):
        for extension, kind in (("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG")):
            with self.subTest(extension=extension):
                image = image_bytes(kind, "RGB")
                partial = services.create_event(CSV, [(f"folder/s-1.{extension}", image)])
                self.assertEqual(self.missing_ids(partial), ["SS-1"])
                complete = services.create_event(CSV, [
                    (f"folder/s-1.{extension}", image), (f"folder/SS-1.{extension}", image)])
                self.assertEqual(complete["missing_avatars"], [])
                self.assertEqual(complete["record_count"], 3)

    def test_custom_tier_and_arbitrary_ticket_id_use_settings(self):
        services.create_tier_template("VIP", "bg.png", image_bytes(), True)
        result = services.create_event(
            "ticket_id,display_name,tier,qr_token\n00001,名字,VIP,token\n".encode(), [])
        self.assertEqual(result["missing_avatars"], [
            {"ticket_id": "00001", "display_name": "名字", "tier": "VIP"}])

    def test_summary_tracks_tier_changes_without_rewriting_attendees(self):
        result = services.create_event(CSV, [])
        root, _ = services.load_event(result["id"])
        original = (root / "event.json").read_bytes()
        services.update_template_settings("sponsors", "Sponsors", False)
        self.assertEqual(self.missing_ids(self.summary(result["id"])), ["SS-1"])
        services.update_template_settings("tier-sponsors", "Sponsors", True)
        self.assertEqual(self.missing_ids(self.summary(result["id"])), ["S-1", "SS-1"])
        services.update_template_settings("attendees", "Attendees", True)
        self.assertEqual(self.missing_ids(self.summary(result["id"])), ["A-1", "S-1", "SS-1"])
        self.assertEqual((root / "event.json").read_bytes(), original)

    def test_removed_avatar_file_and_renamed_tier_follow_rendering_fallback(self):
        result = services.create_event(CSV, [
            ("S-1.png", image_bytes()), ("SS-1.png", image_bytes())])
        self.assertEqual(result["missing_avatars"], [])
        root, event = services.load_event(result["id"])
        services.avatar_path(root, event["records"][1]["avatar_asset"]).unlink()
        services.update_template_settings("sponsors", "Sponsors renamed", True)
        self.assertEqual(self.missing_ids(self.summary(result["id"])), ["S-1"])

    def test_unknown_event_returns_an_api_error(self):
        status, _, body = asyncio.run(
            asgi_request("/api/events/00000000/summary", method="GET"))
        self.assertEqual(status, 400)
        self.assertIn("Event not found", json.loads(body)["detail"])


if __name__ == "__main__":
    unittest.main()
