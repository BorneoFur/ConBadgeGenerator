"""Real small exports plus controlled concurrency; no user's data or large images."""
import asyncio
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import data_management, export_jobs, security, services
from test_upload_security import asgi_request, multipart


CSV = b'ticket_id,display_name,tier,qr_token\nA1,Alice,Attendees,a\nA2,Bob,Attendees,b\n'
PRINT = {'ppi': 72, 'width_mm': 20, 'height_mm': 30, 'bleed_mm': 1}


class ExportJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project' / '.badge_data'
        for target, name, value in ((services, 'DATA_ROOT', self.root),
                                    (services, 'PROJECT_ROOT', self.root.parent),
                                    (export_jobs, '_jobs', {})):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.threads = []
        original = threading.Thread.start

        def start(thread):
            self.threads.append(thread)
            original(thread)

        replacement = patch.object(threading.Thread, 'start', start)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.addCleanup(self.join_threads)
        self.event = services.create_event(CSV, [])

    def join_threads(self):
        for thread in self.threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), 'Export did not finish')

    def request(self, path, method='GET', body=b'', headers=None):
        return asyncio.run(asgi_request(path, body, method=method, headers=headers))

    def start(self, mode='rgb', kind='jpeg', **kwargs):
        return self.request(f"/api/events/{self.event['id']}/export-jobs/{mode}/{kind}", 'POST', **kwargs)

    def test_real_zip_and_pdf_progress_and_native_download(self):
        def badge(*args):
            return Image.new('RGB', (24, 32), 'white'), PRINT

        with patch.object(services, 'render_badge', side_effect=badge):
            for kind in ('jpeg', 'pdf'):
                with self.subTest(kind=kind):
                    status, headers, body = self.start(kind=kind)
                    self.assertEqual(status, 202)
                    self.assertEqual(headers[b'cache-control'], b'no-store')
                    job = json.loads(body)
                    self.join_threads()
                    status, headers, body = self.request(f"/api/export-jobs/{job['id']}")
                    done = json.loads(body)
                    self.assertEqual(status, 200)
                    self.assertEqual(done['status'], 'completed')
                    self.assertEqual((done['completed'], done['total'], done['percent']), (2, 2, 100))
                    self.assertIsNone(done['remaining_seconds'])
                    self.assertIsNone(export_jobs.active())
                    status, headers, body = self.request(done['download_url'])
                    self.assertEqual(status, 200)
                    self.assertIn(b'attachment;', headers[b'content-disposition'])
                    if kind == 'jpeg':
                        with zipfile.ZipFile(io.BytesIO(body)) as archive:
                            self.assertEqual(set(archive.namelist()), {
                                'rgb-jpeg/A1.jpg', 'rgb-jpeg/A2.jpg', 'rgb-pdf/A1.pdf', 'rgb-pdf/A2.pdf'})
                    else:
                        self.assertTrue(body.startswith(b'%PDF'))

    def test_progress_duplicate_routes_and_reset_are_protected_after_start_response(self):
        entered, release, packing, finish = (threading.Event() for _ in range(4))

        def render(event_id, profile, progress):
            progress(0, 2, 'rendering')
            progress(1, 2, 'rendering')
            entered.set()
            self.assertTrue(release.wait(5))
            progress(2, 2, 'packing')
            packing.set()
            self.assertTrue(finish.wait(5))
            output = self.root / 'sample.zip'
            output.write_bytes(b'zip')
            return output

        with patch.object(services, '_export_jpeg_zip', side_effect=render) as renderer:
            try:
                status, _, body = self.start()
                self.assertEqual(status, 202)
                job = json.loads(body)
                self.assertTrue(entered.wait(5))
                # The start request has ended; the background job must still guard data.
                status, _, body = self.request('/api/export-jobs/active')
                current = json.loads(body)['job']
                self.assertEqual(current['id'], job['id'])
                self.assertEqual(current['completed'], 1)
                self.assertGreater(current['percent'], 0)
                self.assertLess(current['percent'], 100)
                self.assertGreater(current['remaining_seconds'], 0)
                for path in (f"/api/events/{self.event['id']}/export-jobs/rgb/pdf",
                             *(f"/api/events/{self.event['id']}/export/{name}" for name in
                               ('rgb-jpeg', 'rgb-pdf', 'cmyk-jpeg', 'cmyk-pdf'))):
                    self.assertEqual(self.request(path, 'POST')[0], 409)
                headers = [(b'host', b'localhost:8000'), (b'origin', b'http://localhost:8000'),
                           (b'x-badge-data-token', data_management.SESSION_TOKEN.encode())]
                for path in ('/api/data/backup', '/api/data/reset'):
                    self.assertEqual(self.request(path, 'POST', headers=headers)[0], 409)
                self.assertEqual(self.request(f"/api/export-jobs/{job['id']}/download")[0], 400)
                release.set()
                self.assertTrue(packing.wait(5))
                current = export_jobs.get(job['id'])
                self.assertEqual(current['stage'], 'packing')
                self.assertLess(current['percent'], 100)
                self.assertIsNone(current['download_url'])
                self.assertIsNone(current['remaining_seconds'])
                self.assertEqual(renderer.call_count, 1)
            finally:
                release.set()
                finish.set()
                self.join_threads()

    def test_failure_unlocks_retry_and_has_no_download(self):
        with patch.object(services, '_export_jpeg_zip', side_effect=services.BadgeError('Invalid printer profile.')), \
                patch.object(export_jobs._logger, 'exception'):
            _, _, body = self.start()
            first = json.loads(body)['id']
            self.join_threads()
            failed = export_jobs.get(first)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['error'], 'Invalid printer profile.')
            self.assertIsNone(failed['download_url'])
            self.assertLess(failed['percent'], 100)
            self.assertEqual(self.start()[0], 202)
            self.join_threads()

    def test_invalid_requests_and_missing_jobs(self):
        self.assertEqual(self.start(mode='invalid')[0], 400)
        self.assertEqual(self.start(kind='invalid')[0], 400)
        self.assertEqual(self.start(mode='cmyk')[0], 400)
        self.assertEqual(self.request('/api/export-jobs/unknown')[0], 404)
        body = multipart([('icc_profile', 'profile.txt', b'bad')])
        self.assertEqual(self.start(mode='cmyk', body=body, headers=[
            (b'content-type', b'multipart/form-data; boundary=audit-boundary')])[0], 400)
        self.assertFalse(export_jobs._jobs)

    def test_cmyk_profile_and_request_id_are_retained(self):
        output = self.root / 'sample.pdf'
        output.write_bytes(b'%PDF')
        identifier = 'a' * 32
        body = (b'--audit-boundary\r\nContent-Disposition: form-data; name="request_id"\r\n\r\n'
                + identifier.encode() + b'\r\n'
                + multipart([('icc_profile', 'printer.icc', b'printer-profile')]))
        with patch.object(services, '_export_master_pdf', return_value=output) as render:
            status, _, content = self.start(mode='cmyk', kind='pdf', body=body, headers=[
                (b'content-type', b'multipart/form-data; boundary=audit-boundary')])
            self.assertEqual(status, 202)
            self.join_threads()
            self.assertEqual(json.loads(content)['id'], identifier)
            self.assertEqual(render.call_args.args, (self.event['id'], b'printer-profile'))
            # A repeated accepted request ID returns the saved result, without rendering again.
            result = export_jobs.start(self.event['id'], 'cmyk', 'pdf', b'printer-profile', identifier)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(render.call_count, 1)

    def test_real_render_failure_removes_partial_files(self):
        for renderer in (services._export_jpeg_zip, services._export_master_pdf):
            with self.subTest(renderer=renderer.__name__), patch.object(services, 'render_badge', side_effect=[
                    (Image.new('RGB', (24, 32), 'white'), PRINT), services.BadgeError('Broken artwork.')]):
                with self.assertRaisesRegex(services.BadgeError, 'Broken artwork'):
                    renderer(self.event['id'])
                self.assertEqual(list((self.root / 'events' / self.event['id'] / 'exports').iterdir()), [])

    def test_render_callbacks_report_each_badge_then_finalization(self):
        for renderer in (services._export_jpeg_zip, services._export_master_pdf):
            progress = []
            with patch.object(services, 'render_badge', side_effect=lambda *args: (Image.new('RGB', (24, 32)), PRINT)):
                renderer(self.event['id'], progress=lambda *values: progress.append(values))
            self.assertEqual(progress, [(0, 2, 'rendering'), (1, 2, 'rendering'),
                                        (2, 2, 'rendering'), (2, 2, 'packing')])

    def test_stop_during_badge_cleans_output_preserves_previous_files_and_allows_retry(self):
        event_root = self.root / 'events' / self.event['id']
        records = (event_root / 'event.json').read_bytes()
        output_dir = event_root / 'exports'
        output_dir.mkdir()
        previous = output_dir / 'previous.zip'
        previous.write_bytes(b'previous export')
        for kind in ('jpeg', 'pdf'):
            entered, release = threading.Event(), threading.Event()

            def badge(*args):
                entered.set()
                self.assertTrue(release.wait(5))
                return Image.new('RGB', (24, 32), 'white'), PRINT

            with self.subTest(kind=kind), patch.object(services, 'render_badge', side_effect=badge) as render:
                before = set(output_dir.iterdir())
                try:
                    _, _, body = self.start(kind=kind)
                    identifier = json.loads(body)['id']
                    self.assertTrue(entered.wait(5))
                    for _ in range(2):
                        status, headers, body = self.request(f'/api/export-jobs/{identifier}/cancel', 'POST')
                        stopping = json.loads(body)
                        self.assertEqual(status, 200)
                        self.assertEqual(headers[b'cache-control'], b'no-store')
                        self.assertEqual(stopping['stage'], 'stopping')
                        self.assertTrue(stopping['cancel_requested'])
                        self.assertIsNone(stopping['remaining_seconds'])
                    self.assertEqual(export_jobs.active()['id'], identifier)
                    self.assertEqual(self.start()[0], 409)
                finally:
                    release.set()
                    self.join_threads()
                stopped = export_jobs.get(identifier)
                self.assertEqual(stopped['status'], 'cancelled')
                self.assertEqual(stopped['completed'], 1)
                self.assertLess(stopped['percent'], 100)
                self.assertIsNone(stopped['download_url'])
                self.assertIsNone(stopped['error'])
                self.assertEqual(render.call_count, 1)
                self.assertEqual(set(output_dir.iterdir()), before)
                self.assertEqual(previous.read_bytes(), b'previous export')
                self.assertEqual((event_root / 'event.json').read_bytes(), records)
                self.assertEqual(self.request(f'/api/export-jobs/{identifier}/download')[0], 400)
                self.assertIsNone(export_jobs.active())
                status, _, body = self.start(kind=kind)
                self.assertEqual(status, 202)
                self.join_threads()
                self.assertEqual(export_jobs.get(json.loads(body)['id'])['status'], 'completed')

    def test_stop_while_waiting_for_image_lock_renders_no_badges(self):
        with patch.object(services, 'render_badge') as render:
            with security._IMAGE_LOCK:
                _, _, body = self.start()
                identifier = json.loads(body)['id']
                self.request(f'/api/export-jobs/{identifier}/cancel', 'POST')
            self.join_threads()
            self.assertEqual(export_jobs.get(identifier)['status'], 'cancelled')
            render.assert_not_called()

    def test_stop_during_finalization_discards_just_finished_file(self):
        for kind in ('jpeg', 'pdf'):
            entered, release = threading.Event(), threading.Event()
            output = self.root / f'late-output.{kind}'

            def render(event_id, profile, progress):
                progress(2, 2, 'packing')
                output.write_bytes(b'finished file')
                entered.set()
                self.assertTrue(release.wait(5))
                return output

            renderer = '_export_master_pdf' if kind == 'pdf' else '_export_jpeg_zip'
            with self.subTest(kind=kind), patch.object(services, renderer, side_effect=render):
                try:
                    _, _, body = self.start(kind=kind)
                    identifier = json.loads(body)['id']
                    self.assertTrue(entered.wait(5))
                    export_jobs.cancel(identifier)
                finally:
                    release.set()
                    self.join_threads()
                self.assertEqual(export_jobs.get(identifier)['status'], 'cancelled')
                self.assertFalse(output.exists())

    def test_stop_after_completion_keeps_valid_download_and_unknown_task_returns_404(self):
        output = self.root / 'finished.zip'
        output.write_bytes(b'finished file')
        with patch.object(services, '_export_jpeg_zip', return_value=output):
            _, _, body = self.start()
            identifier = json.loads(body)['id']
            self.join_threads()
            status, _, body = self.request(f'/api/export-jobs/{identifier}/cancel', 'POST')
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertEqual(result['status'], 'completed')
            self.assertFalse(result['cancel_requested'])
            self.assertEqual(output.read_bytes(), b'finished file')
            self.assertEqual(self.request(result['download_url'])[0], 200)
        self.assertEqual(self.request('/api/export-jobs/unknown/cancel', 'POST')[0], 404)


if __name__ == '__main__':
    unittest.main()
