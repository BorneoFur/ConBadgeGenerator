"""Regression checks use small, harmless files; no exploit payloads or large allocations."""
import asyncio
import base64
import copy
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image, PngImagePlugin

from app import main, security, services
from font_fixtures import sample_font


CSV = b"ticket_id,display_name,tier,qr_token\nA1,Alice,Sponsors,token\n"
# Ordinary uncompressed 2x2 RGB PSD, used only to check format rejection.
PSD = (struct.pack('>4sH6sHIIHH', b'8BPS', 1, b'\0' * 6, 3, 2, 2, 8, 3)
       + b'\0' * 14 + b'\xff' * 4 + b'\0' * 8)


def image_bytes(kind="PNG", mode="RGBA", size=(12, 16), **options):
    image = Image.new(mode, size, (20, 40, 60, 100) if mode == "RGBA" else "red")
    output = io.BytesIO()
    image.save(output, kind, **options)
    return output.getvalue()


def multipart(files):
    data = bytearray()
    for field, filename, content in files:
        data.extend(f'--audit-boundary\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        data.extend(content)
        data.extend(b'\r\n')
    data.extend(b'--audit-boundary--\r\n')
    return bytes(data)


async def asgi_request(path, body=b"", method="POST", headers=None, chunks=None):
    messages = []
    parts = list(chunks) if chunks is not None else [body]

    async def receive():
        if not parts:
            return {'type': 'http.disconnect'}
        return {'type': 'http.request', 'body': parts.pop(0), 'more_body': bool(parts)}

    async def send(message):
        messages.append(message)

    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
             'query_string': b'', 'root_path': '', 'headers': headers or [],
             'client': ('127.0.0.1', 12345), 'server': ('127.0.0.1', 8000)}
    await main.app(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    content = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return start['status'], dict(start['headers']), content


class UploadSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        data = patch.object(services, 'DATA_ROOT', self.root / 'data')
        data.start()
        self.addCleanup(data.stop)

    def template_package(self, assets=(), **changes):
        manifest = copy.deepcopy(services.discover_templates()['attendees']['manifest'])
        manifest.update(name='Audit', elements=[], **changes)
        return json.dumps({'format_version': 1, 'templates': [{'manifest': manifest, 'assets': [
            {'path': name, 'base64_data': base64.b64encode(content).decode()} for name, content in assets
        ]}]}).encode()

    def test_disguised_formats_rejected_by_every_upload_path(self):
        for content in (PSD, image_bytes('GIF', 'RGB'), b'plain text'):
            for filename in ('image.png', 'image.jpg'):
                calls = [lambda: services.create_tier_template('Audit', filename, content, False),
                         lambda: services.update_template_background('attendees', filename, content),
                         lambda: services.add_template_asset('attendees', 'mask', filename, content),
                         lambda: services.add_template_asset('attendees', 'artwork', filename, content),
                         lambda: services.create_event(CSV, [('A1' + Path(filename).suffix, content)]),
                         lambda: services.import_template_set_json(self.template_package([(filename, content)]))]
                for index, call in enumerate(calls):
                    with self.subTest(filename=filename, entry=index, signature=content[:4]), self.assertRaises(services.BadgeError):
                        call()
        self.assertFalse(list((self.root / 'data').glob('events/*')))

    def test_truncated_mismatched_and_animated_images_rejected(self):
        jpeg = image_bytes('JPEG', 'RGB', size=(40, 40))
        animation = io.BytesIO()
        Image.new('RGBA', (2, 2), 'red').save(animation, 'PNG', save_all=True,
                                           append_images=[Image.new('RGBA', (2, 2), 'blue')])
        for name, content in [('bad.jpg', jpeg[:-20]), ('wrong.jpg', image_bytes()),
                              ('animation.png', animation.getvalue())]:
            with self.subTest(name=name), self.assertRaises(services.BadgeError):
                security.normalize_image(name, content)

    def test_png_alpha_dpi_and_pixels_survive_metadata_removal(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('Comment', 'audit-metadata-marker')
        original = image_bytes(pnginfo=metadata, dpi=(300, 300))
        normalized, width, height, ppi = security.normalize_image('a.PNG', original + b'audit-tail-marker')
        self.assertEqual((width, height, ppi), (12, 16, 300))
        self.assertNotIn(b'audit-metadata-marker', normalized)
        self.assertNotIn(b'audit-tail-marker', normalized)
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.getpixel((0, 0)), (20, 40, 60, 100))
            self.assertAlmostEqual(image.info['dpi'][0], 300, delta=.1)

    def test_jpeg_orientation_is_preserved_without_exif_or_trailing_data(self):
        exif = Image.Exif()
        exif[274] = 6
        original = image_bytes('JPEG', 'RGB', exif=exif, dpi=(150, 150))
        normalized, width, height, ppi = security.normalize_image('a.JPEG', original + b'audit-tail-marker')
        self.assertEqual((width, height, ppi), (16, 12, 150))
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertFalse(image.getexif())
        self.assertNotIn(b'audit-tail-marker', normalized)

    def test_file_and_pixel_limits_precede_pixel_loading(self):
        content = image_bytes()
        with patch.object(security, 'MAX_IMAGE_BYTES', 8), self.assertRaises(services.BadgeError):
            security.normalize_image('a.png', content)
        with patch.object(security, 'MAX_IMAGE_PIXELS', 100), patch('PIL.PngImagePlugin.PngImageFile.load') as load:
            with self.assertRaises(services.BadgeError):
                security.normalize_image('a.png', content)
            load.assert_not_called()

    def test_avatar_normalization_and_failed_import_cleanup(self):
        event = services.create_event(CSV, [('folder/A1.png', image_bytes() + b'audit-tail-marker'),
                                           ('folder/extra.py', b'harmless text')])
        path = services.event_avatar_preview(event['id'], 'A1')
        self.assertNotIn(b'audit-tail-marker', path.read_bytes())
        self.assertFalse(path.with_name('extra.py').exists())
        before = set((self.root / 'data/events').iterdir())
        with self.assertRaises(services.BadgeError):
            services.create_event(CSV, [('A1.png', image_bytes()), ('A2.jpg', PSD)])
        self.assertEqual(before, set((self.root / 'data/events').iterdir()))

    def test_old_avatars_are_checked_at_preview_and_render(self):
        event = services.create_event(CSV, [('A1.jpg', image_bytes('JPEG', 'RGB'))])
        path = services.event_avatar_preview(event['id'], 'A1')
        path.write_bytes(PSD)
        with self.assertRaises(HTTPException) as error:
            main.editor_avatar_preview(event['id'], 'A1')
        self.assertEqual(error.exception.status_code, 400)
        event_dir, stored = services.load_event(event['id'])
        with self.assertRaises(services.BadgeError):
            services.render_badge(event_dir, stored['records'][0])

    def test_template_image_roundtrip_and_legacy_decoder_guards(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('Comment', 'audit-metadata-marker')
        content = image_bytes(size=(120, 160), pnginfo=metadata, dpi=(150, 150))
        result = services.create_tier_template('Audit', 'background.png', content, False)
        background = services.template_background_path(result['id'])
        self.assertEqual(result['ppi'], 150)
        self.assertNotIn(b'audit-metadata-marker', background.read_bytes())
        services.save_template_layout(result['id'], [])
        services.import_template_set_json(services.template_set_json())
        background = services.template_background_path(result['id'])
        response = main.template_background_preview(result['id'])
        with Image.open(io.BytesIO(response.body)) as image:
            self.assertEqual(image.getpixel((0, 0)), (20, 40, 60, 100))
        asset = services.add_template_asset(result['id'], 'mask', 'mask.png', content)
        path = services.template_asset_path(result['id'], asset['asset'])
        path.write_bytes(PSD)
        with self.assertRaises(HTTPException):
            main.template_mask_preview(result['id'], asset['asset'])
        artwork = {'type': 'artwork', 'asset': asset['asset'], 'x_mm': 0, 'y_mm': 0,
                   'width_mm': 10, 'height_mm': 10}
        services.save_template_layout(result['id'], [artwork])
        with self.assertRaises(services.BadgeError):
            services.render_badge(self.root, {'tier': 'Audit'})
        services.save_template_layout(result['id'], [])
        background.write_bytes(PSD)
        with self.assertRaises(HTTPException):
            main.template_background_preview(result['id'])
        with self.assertRaises(services.BadgeError):
            services.render_badge(self.root, {'tier': 'Audit'})

    def test_other_image_decoders_are_never_entered(self):
        Image.init()
        with patch('PIL.PsdImagePlugin.PsdImageFile._open', side_effect=AssertionError('PSD decoder was entered')):
            with self.assertRaises(services.BadgeError):
                security.normalize_image('avatar.jpg', PSD)

    def test_template_html_and_other_active_content_rejected(self):
        for name in ('page.html', 'script.js', 'image.svg', 'file.py', '../escape.png', 'C:/escape.png'):
            with self.subTest(name=name), self.assertRaises(services.BadgeError):
                services.import_template_set_json(self.template_package([(name, b'harmless text')]))
        self.assertFalse((self.root / 'data/templates/tier-audit').exists())

    def test_legacy_resource_routes_do_not_serve_html_or_disguised_images(self):
        result = services.save_template_layout('attendees', [])
        root = services.discover_templates()[result['id']]['root']
        (root / 'page.html').write_text('<p>harmless</p>')
        (root / 'fake.png').write_text('<p>harmless</p>')
        for name in ('page.html', 'fake.png'):
            with self.subTest(name=name), self.assertRaises(HTTPException):
                main.template_asset_preview(result['id'], name)
        (root / 'license.txt').write_text('License')
        response = main.template_asset_preview(result['id'], 'license.txt')
        self.assertEqual(response.media_type, 'application/octet-stream')
        self.assertIn('attachment', response.headers['content-disposition'])

    def test_font_traversal_is_rejected_before_writing(self):
        for identifier in ('../../../escaped', '/tmp/escaped', r'..\escaped', 'C:escaped'):
            font = {'id': identifier, 'name': 'AuditFont'}
            with self.subTest(identifier=identifier), self.assertRaises(services.BadgeError):
                services.save_template_layout('attendees', [], [font])
            with self.assertRaises(services.BadgeError):
                services.import_template_set_json(self.template_package(fonts=[font]))
        # Defense in depth: even a legacy/unchecked manifest cannot influence the write path.
        template = copy.deepcopy(services.discover_templates()['attendees'])
        template['manifest']['fonts'] = [{'id': '../../../escaped', 'name': 'AuditFont'}]
        stream = io.BytesIO()
        sample_font('AuditFont').save(stream)
        with patch.object(services, 'discover_templates', return_value={'attendees': template}):
            with self.assertRaises(services.BadgeError):
                services.add_template_font('attendees', 'AuditFont', 'regular', 'valid.ttf', stream.getvalue())
        self.assertFalse((self.root / 'escaped-regular.ttf').exists())

    def test_symlinks_cannot_escape_asset_directory(self):
        (self.root / 'assets').mkdir()
        (self.root / 'outside.png').write_bytes(image_bytes())
        (self.root / 'assets/link.png').symlink_to(self.root / 'outside.png')
        with self.assertRaises(services.BadgeError):
            security.asset_path(self.root / 'assets', 'link.png')

    def test_huge_nonfinite_and_boolean_geometry_rejected_before_allocation(self):
        template = copy.deepcopy(services.discover_templates()['attendees'])
        for value in (1_000_000, float('inf'), float('nan'), True):
            with self.subTest(value=value):
                template['manifest']['print']['width_mm'] = value
                with patch.object(services.Image, 'new') as allocation, self.assertRaises(services.BadgeError):
                    services.render_badge(self.root, {'tier': 'Attendees'}, {'attendees': template})
                allocation.assert_not_called()
        element = {'type': 'text', 'field': 'display_name', 'x_mm': 0, 'y_mm': 0,
                   'width_mm': 1000, 'height_mm': 1000}
        with self.assertRaises(services.BadgeError):
            services.save_template_layout('attendees', [element])

    def test_avatar_resize_limit_blocks_extreme_aspect_ratios(self):
        event = services.create_event(CSV, [('A1.png', image_bytes(size=(1000, 1)))])
        with patch.object(Image.Image, 'resize') as resize, self.assertRaises(services.BadgeError):
            event_dir, stored = services.load_event(event['id'])
            services.render_badge(event_dir, stored['records'][0])
        resize.assert_not_called()

    def test_spreadsheet_and_template_limits_are_enforced(self):
        from test_spreadsheet_import import xlsx_bytes, HEADERS
        data = xlsx_bytes([HEADERS, ['1', 'A1', 'Name', 'Attendees', 'token']])
        with patch.object(security, 'MAX_RECORDS', 0), self.assertRaises(services.BadgeError):
            services.attendee_file_to_csv('attendees.xlsx', data)
        with patch.object(security, 'MAX_SPREADSHEET_BYTES', 8), self.assertRaises(services.BadgeError):
            services.attendee_file_to_csv('attendees.csv', CSV)
        with patch.object(security, 'MAX_TEMPLATE_BYTES', 8), self.assertRaises(services.BadgeError):
            services.import_template_set_json(self.template_package())

    def test_http_multipart_upload_and_preview(self):
        data = multipart([('csv_file', 'attendees.csv', CSV), ('avatar_files', 'A1.png', image_bytes())])
        headers = [(b'content-type', b'multipart/form-data; boundary=audit-boundary')]
        status, response_headers, content = asyncio.run(asgi_request('/api/import', data, headers=headers))
        self.assertEqual(status, 200, content)
        self.assertEqual(response_headers[b'x-content-type-options'], b'nosniff')
        event = json.loads(content)
        status, response_headers, content = asyncio.run(asgi_request(f"/api/events/{event['id']}/records/A1/avatar", method='GET'))
        self.assertEqual(status, 200, content)
        self.assertEqual(response_headers[b'content-type'], b'image/png')
        self.assertTrue(content.startswith(b'\x89PNG'))
        forged = multipart([('csv_file', 'attendees.csv', CSV), ('avatar_files', 'A1.jpg', PSD)])
        status, _, _ = asyncio.run(asgi_request('/api/import', forged, headers=headers))
        self.assertEqual(status, 400)

    def test_http_limits_include_chunked_and_false_content_length(self):
        with patch.object(security, 'MAX_REQUEST_BYTES', 10):
            for headers, chunks in [([(b'content-length', b'11')], [b'']),
                                    ([], [b'12345', b'123456']),
                                    ([(b'content-length', b'2')], [b'12345', b'123456'])]:
                with self.subTest(headers=headers):
                    status, _, _ = asyncio.run(asgi_request('/api/import', headers=headers, chunks=chunks))
                    self.assertEqual(status, 413)
        uploaded = UploadFile(filename='a.png', file=io.BytesIO(b'12345'))
        with self.assertRaises(HTTPException) as error:
            asyncio.run(main.read_upload(uploaded, 4))
        self.assertEqual(error.exception.status_code, 413)

    def test_rgb_jpeg_pdf_and_zip_exports_with_sanitized_avatar(self):
        avatar = {'type': 'masked-image', 'field': 'avatar_asset', 'x_mm': 0, 'y_mm': 0,
                  'width_mm': 20, 'height_mm': 20, 'fit': 'contain'}
        services.save_template_layout('sponsors', [avatar])
        event = services.create_event(CSV, [('A1.png', image_bytes())])
        jpg, _ = services.export_badge(event['id'], 'A1', 'jpg')
        pdf, _ = services.export_badge(event['id'], 'A1', 'pdf')
        self.assertTrue(jpg.startswith(b'\xff\xd8'))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertTrue(services.export_rgb_jpeg_zip(event['id']).is_file())
        self.assertTrue(services.export_rgb_master_pdf(event['id']).is_file())


if __name__ == '__main__':
    unittest.main()
