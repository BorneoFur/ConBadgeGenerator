import asyncio
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

from app import services
from app.main import import_event


HEADERS = ["number", "ticket_id", "display_name", "tier", "qr_token"]


def xlsx_bytes(rows, second_rows=None):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    if second_rows:
        second = book.create_sheet("Ignored")
        for row in second_rows:
            second.append(row)
        book.active = 1
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


class SpreadsheetImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = patch.object(services, "DATA_ROOT", Path(self.directory.name))
        self.root.start()
        self.addCleanup(self.root.stop)

    def upload(self, filename, data):
        return asyncio.run(import_event(UploadFile(filename=filename, file=io.BytesIO(data)), []))

    def test_examples_import_identically_through_endpoint(self):
        records = []
        for suffix in ("csv", "xlsx"):
            sample = Path(f"app/static/example-attendees.{suffix}").read_bytes()
            result = self.upload(f"EXAMPLE.{suffix.upper()}", sample)
            self.assertEqual(result["record_count"], 3)
            _, event = services.load_event(result["id"])
            records.append(event["records"])
        self.assertEqual(*records)

    def test_first_sheet_and_text_preservation(self):
        token = '  000123, "quoted"\n你好  '
        rows = [HEADERS, [1, "00001", "名字, Example", "Attendees", token],
                [None] * 5, [3, 123, "Other", "Sponsors", 456]]
        data = xlsx_bytes(rows, [HEADERS, [1, "IGNORED", "Ignored", "Attendees", "token"]])
        converted = services.attendee_file_to_csv("example.xlsx", data)
        parsed = list(csv.DictReader(io.StringIO(converted.decode())))
        self.assertEqual(parsed[0]["ticket_id"], "00001")
        self.assertEqual(parsed[0]["qr_token"], token)
        result = self.upload("example.xlsx", data)
        self.assertEqual(result["record_count"], 2)
        _, event = services.load_event(result["id"])
        self.assertEqual(event["records"][0]["qr_token"], token)
        self.assertEqual(event["records"][1]["ticket_id"], "123")

    def test_invalid_uploads_return_400(self):
        examples = [("bad.xlsx", b"not a workbook"), ("old.xls", b"anything"),
                    ("empty.xlsx", xlsx_bytes([])),
                    ("headers.xlsx", xlsx_bytes([["wrong"]])),
                    ("duplicate.xlsx", xlsx_bytes([HEADERS, [1, "A", "One", "Attendees", "x"],
                                                   [2, "A", "Two", "Attendees", "y"]])),
                    ("formula.xlsx", xlsx_bytes([HEADERS, [1, "A", "One", "Attendees", "=1+1"]])),
                    ("error.xlsx", xlsx_bytes([HEADERS, [1, "A", "One", "Attendees", "#DIV/0!"]]))]
        for filename, data in examples:
            with self.subTest(filename=filename), self.assertRaises(HTTPException) as error:
                self.upload(filename, data)
            self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(list(Path(self.directory.name).glob("events/*")))

    def test_csv_passes_through_unchanged(self):
        content = b"\xef\xbb\xbf" + Path("app/static/example-attendees.csv").read_bytes()
        self.assertEqual(services.attendee_file_to_csv("example.csv", content), content)
        self.assertEqual(self.upload("example.csv", content)["record_count"], 3)


if __name__ == "__main__":
    unittest.main()
