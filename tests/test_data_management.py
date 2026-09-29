"""Backup/reset checks use isolated temporary data; never the user's saved files."""
import asyncio
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import data_management as data, security, services, typography
from test_upload_security import CSV, asgi_request, image_bytes


class DataManagementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.project = self.directory / 'project'
        self.root = self.project / '.badge_data'
        self.root.mkdir(parents=True)
        for target, name, value in ((services, 'DATA_ROOT', self.root),
                                    (services, 'PROJECT_ROOT', self.project), (data, '_backups', {})):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def save(self, path, content=b'saved data'):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    def request(self, path, payload=None, method='POST', **headers):
        if path == '/api/data/reset':
            payload = {'data_root': str(self.root.resolve()), **(payload or {})}
        values = {'host': '127.0.0.1:8000', 'origin': 'http://127.0.0.1:8000',
                  'content-type': 'application/json', 'x-badge-data-token': data.SESSION_TOKEN,
                  **headers}
        return asyncio.run(asgi_request(path, json.dumps(payload or {}).encode(), method=method,
                                       headers=[(key.encode(), value.encode()) for key, value in values.items()]))

    def test_backup_survives_corrupt_layout_and_excludes_imports_transient_and_linked_files(self):
        files = {'templates/custom/template.json': b'{invalid json',
                 'templates/custom/assets/background.png': b'saved artwork bytes',
                 'templates/custom/assets/mask.png': b'saved mask bytes',
                 'events/event/exports/badges.zip': b'saved export bytes',
                 'events/event/masters/A1.png': b'saved generated badge bytes',
                 'google-fonts/test.ttf': b'saved font bytes'}
        for path, content in files.items():
            self.save(path, content)
        self.save('.uploads/unfinished')
        self.save('.staging-import/unfinished')
        self.save('.backups/old/archive.zip')
        excluded = {'events/event/event.json': b'{damaged attendee records',
                    'events/event/assets/A1.png': b'imported avatar bytes',
                    'events/event/assets/nested/A2.jpg': b'another imported avatar',
                    'events/another-event/event.json': b'{"records": []}',
                    'events/another-event/assets/A3.png': b'avatar from another event'}
        for name, content in excluded.items():
            self.save(name, content)
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'outside data')
        (self.root / 'linked-folder').symlink_to(outside, target_is_directory=True)
        (self.root / 'linked-file').symlink_to(outside / 'secret')
        self.save('..\\unsafe.txt')
        result = data.create_backup()
        archive_path, filename = data.get_backup(result['download_url'].rsplit('/', 1)[1])
        self.assertTrue(filename.endswith('.zip'))
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {'.badge_data/', 'RESTORE.txt',
                                                      *('.badge_data/' + path for path in files)})
            for path, content in files.items():
                self.assertEqual(archive.read('.badge_data/' + path), content)
                self.assertEqual((self.root / path).read_bytes(), content)
            self.assertIn(b'unsafe.txt', archive.read('RESTORE.txt'))
            self.assertIn(b'Imported attendee lists', archive.read('RESTORE.txt'))
            for name, content in excluded.items():
                self.assertNotIn('.badge_data/' + name, archive.namelist())
                self.assertEqual((self.root / name).read_bytes(), content)

    def test_backup_api_download_and_cleanup(self):
        event = services.create_event(CSV, [('A1.png', image_bytes())])
        event_root = self.root / 'events' / event['id']
        roster_before = (event_root / 'event.json').read_bytes()
        avatar_before = (event_root / 'assets/A1.png').read_bytes()
        self.save(f"events/{event['id']}/exports/badges.zip", b'saved export')
        self.save('templates/layout.json', b'{corrupt')
        status, headers, content = self.request('/api/data/session', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(headers[b'cache-control'], b'no-store')
        self.assertEqual(json.loads(content)['token'], data.SESSION_TOKEN)
        status, _, content = self.request('/api/data/backup')
        self.assertEqual(status, 200)
        url = json.loads(content)['download_url']
        status, headers, content = self.request(url, method='GET')
        self.assertEqual(status, 200)
        self.assertIn(b'attachment;', headers[b'content-disposition'])
        self.assertEqual(headers[b'cache-control'], b'no-store')
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(archive.read('.badge_data/templates/layout.json'), b'{corrupt')
            prefix = f".badge_data/events/{event['id']}/"
            self.assertEqual(archive.read(prefix + 'exports/badges.zip'), b'saved export')
            self.assertNotIn(prefix + 'event.json', archive.namelist())
            self.assertFalse(any(name.startswith(prefix + 'assets/') for name in archive.namelist()))
        self.assertEqual((event_root / 'event.json').read_bytes(), roster_before)
        self.assertEqual((event_root / 'assets/A1.png').read_bytes(), avatar_before)
        self.assertFalse(data._backups)
        self.assertFalse(list((self.root / '.backups').iterdir()))
        self.assertTrue((self.root / 'templates/layout.json').exists())
        self.assertEqual(self.request(url, method='GET')[0], 400)

    def test_empty_backup_and_expired_download(self):
        result = data.create_backup()
        identifier = result['download_url'].rsplit('/', 1)[1]
        path, filename, _ = data._backups[identifier]
        data._backups[identifier] = (path, filename, time.monotonic() - 1)
        with self.assertRaises(security.BadgeError):
            data.get_backup(identifier)
        self.assertFalse(path.exists())

    def test_reset_removes_all_saved_data_but_keeps_root_and_external_link_targets(self):
        for path in ('templates/custom/layout.json', 'events/test/avatar.png', '.uploads/stale',
                     'google-fonts/test.ttf', '.staging-00000000-0000-0000-0000-000000000000/data',
                     '.backups/old/backup.zip'):
            self.save(path)
        outside = self.directory / 'external.txt'
        outside.write_bytes(b'keep me')
        (self.root / 'events/linked-file').symlink_to(outside)
        with patch.object(typography, 'clear_font_cache') as clear:
            status, _, content = self.request('/api/data/reset', {'confirmation': 'DELETE'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {'reset': True, 'preserved_entries': []})
        self.assertTrue(self.root.is_dir())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(outside.read_bytes(), b'keep me')
        clear.assert_called_once()
        self.assertTrue(services.discover_templates())

    def test_reset_requires_exact_confirmation_and_valid_token(self):
        saved = self.save('templates/layout.json')
        for confirmation in ('', 'delete', 'DELETE ', True):
            self.assertEqual(self.request('/api/data/reset', {'confirmation': confirmation})[0], 400)
            self.assertTrue(saved.exists())
        self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE'},
                                      **{'x-badge-data-token': ''})[0], 403)
        self.assertEqual(self.request('/api/data/backup', **{'x-badge-data-token': 'wrong'})[0], 403)
        self.assertTrue(saved.exists())

    def test_untrusted_hosts_and_origins_cannot_obtain_token_backup_or_reset(self):
        saved = self.save('templates/layout.json')
        for headers in ({'host': 'evil.example:8000'}, {'host': ''},
                        {'origin': 'https://evil.example'}, {'origin': 'null'},
                        {'origin': 'http://127.0.0.1:9999'}, {'sec-fetch-site': 'cross-site'},
                        {'sec-fetch-site': 'same-site'}):
            for path, method in (('/api/data/session', 'GET'), ('/api/data/backup', 'POST'),
                                 ('/api/data/reset', 'POST')):
                with self.subTest(headers=headers, path=path):
                    self.assertEqual(self.request(path, {'confirmation': 'DELETE'}, method, **headers)[0], 403)
        self.assertTrue(saved.exists())

    def test_loopback_hosts_are_supported(self):
        for host in ('localhost:8000', '127.0.0.1:8000', '[::1]:8000'):
            self.assertEqual(self.request('/api/data/session', method='GET', host=host,
                                          origin='http://' + host)[0], 200)

    def test_unsafe_data_roots_are_rejected_before_any_write(self):
        link = self.directory / 'symlink'
        link.symlink_to(self.root, target_is_directory=True)
        file = self.directory / 'file'
        file.write_text('keep me')
        roots = [link, file, Path('/'), Path.home(), Path(tempfile.gettempdir()),
                 services.PROJECT_ROOT, services.PROJECT_ROOT / 'app/static']
        for root in roots:
            with self.subTest(root=root), patch.object(services, 'DATA_ROOT', root), \
                    patch('app.http_security.upload_directory') as upload:
                self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE'})[0], 400)
                self.assertEqual(self.request('/api/data/backup')[0], 400)
                upload.assert_not_called()
        self.assertEqual(file.read_text(), 'keep me')

    def test_linked_backup_folder_is_rejected(self):
        saved = self.save('templates/layout.json')
        (self.root / '.backups').symlink_to(saved.parent, target_is_directory=True)
        with self.assertRaises(security.BadgeError):
            data.create_backup()
        self.assertTrue(saved.exists())

    def test_nested_mounts_are_rejected_before_backup_or_deletion(self):
        saved = self.save('mounted/data')
        with patch.object(Path, 'is_mount', lambda path: path == saved.parent.resolve()):
            for action in (data.create_backup, lambda: data.reset_data(str(self.root.resolve()))):
                with self.assertRaises(security.BadgeError):
                    action()
        self.assertTrue(saved.exists())

    def test_reset_reports_partial_failure(self):
        self.save('templates/layout.json')
        with patch.object(data.shutil, 'rmtree', side_effect=PermissionError('test')):
            status, _, content = self.request('/api/data/reset', {'confirmation': 'DELETE'})
        self.assertEqual(status, 400)
        self.assertIn('Some data may already be deleted', json.loads(content)['detail'])

    def test_custom_directory_is_never_authorized_for_reset_by_environment_configuration(self):
        outside = self.directory / 'personal-documents'
        outside.mkdir()
        important = outside / 'templates'
        important.mkdir()
        (important / 'important.txt').write_text('keep me')
        with patch.object(services, 'DATA_ROOT', outside), patch('app.http_security.upload_directory') as upload:
            self.assertEqual(self.request('/api/data/reset-preview', method='GET')[0], 400)
            self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE',
                                                              'data_root': str(outside.resolve())})[0], 400)
            upload.assert_not_called()
            result = data.create_backup()
            self.assertIn('download_url', result)
        self.assertEqual((important / 'important.txt').read_text(), 'keep me')

    def test_reset_preserves_unrecognized_root_entries_and_reports_the_scope(self):
        self.save('templates/layout.json')
        self.save('.staging-template-set-00000000-0000-0000-0000-000000000000/stale')
        preserved = ['personal.txt', 'photos', '.staging-personal', '.staging-00000000-0000-0000-0000-000000000000-extra']
        self.save('personal.txt', b'keep me')
        for name in preserved[1:]:
            self.save(name + '/original.txt', b'keep me')
        status, headers, content = self.request('/api/data/reset-preview', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(headers[b'cache-control'], b'no-store')
        preview = json.loads(content)
        self.assertEqual(preview['data_root'], str(self.root.resolve()))
        self.assertEqual(preview['preserved_entries'], sorted(preserved))
        self.assertEqual(preview['delete_entries'], ['.staging-template-set-00000000-0000-0000-0000-000000000000', 'templates'])
        status, _, content = self.request('/api/data/reset', {'confirmation': 'DELETE'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)['preserved_entries'], sorted(preserved))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), sorted(preserved))
        self.assertEqual((self.root / 'personal.txt').read_bytes(), b'keep me')
        for name in preserved[1:]:
            self.assertEqual((self.root / name / 'original.txt').read_bytes(), b'keep me')

    def test_client_cannot_choose_another_directory_or_skip_target_confirmation(self):
        saved = self.save('events/important.txt')
        for target in (None, '/', str(self.directory), str(self.root / '..')):
            self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE', 'data_root': target})[0], 400)
            self.assertTrue(saved.exists())

    def test_known_symlinks_are_unlinked_without_touching_external_directories(self):
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'important.txt').write_text('keep me')
        (self.root / 'events').symlink_to(outside, target_is_directory=True)
        (self.root / 'unrelated-link').symlink_to(outside, target_is_directory=True)
        data.reset_data(str(self.root.resolve()))
        self.assertFalse((self.root / 'events').exists())
        self.assertTrue((self.root / 'unrelated-link').is_symlink())
        self.assertEqual((outside / 'important.txt').read_text(), 'keep me')

    def test_symlinked_parent_cannot_redirect_reset_to_an_unrelated_folder(self):
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'important.txt').write_text('keep me')
        alias = self.directory / 'alias'
        alias.symlink_to(outside, target_is_directory=True)
        with patch.object(services, 'DATA_ROOT', alias / 'data'):
            self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE'})[0], 400)
        self.assertEqual(list(outside.iterdir()), [outside / 'important.txt'])

    def test_reset_refuses_a_root_swapped_for_a_symlink_before_open(self):
        self.save('events/saved.txt')
        outside = self.directory / 'outside'
        (outside / 'events').mkdir(parents=True)
        (outside / 'events/important.txt').write_text('keep me')
        original_open = os.open

        def replace_root(path, flags, *args, **kwargs):
            self.root.rename(self.project / 'held-data')
            self.root.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(data.os, 'open', replace_root), self.assertRaises(security.BadgeError):
            data.reset_data(str(self.root.resolve()))
        self.assertEqual((outside / 'events/important.txt').read_text(), 'keep me')
        self.assertTrue((self.project / 'held-data/events/saved.txt').exists())

    def test_mounts_including_linux_bind_mounts_block_reset_before_deletion(self):
        saved = self.save('events/saved.txt')
        for mount in (self.root.resolve(), (self.root / 'events').resolve()):
            with patch.object(data, 'linux_mount_points', return_value={mount}), self.assertRaises(security.BadgeError):
                data.reset_data(str(self.root.resolve()))
            self.assertTrue(saved.exists())

    def test_child_replaced_with_symlink_cannot_redirect_recursive_deletion(self):
        self.save('events/saved.txt')
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'important.txt').write_text('keep me')
        original_rmtree = data.shutil.rmtree

        def replace_child(path, *args, **kwargs):
            (self.root / 'events').rename(self.root / 'held-events')
            (self.root / 'events').symlink_to(outside, target_is_directory=True)
            return original_rmtree(path, *args, **kwargs)

        replace_child.avoids_symlink_attacks = True
        with patch.object(data.shutil, 'rmtree', replace_child), self.assertRaises(security.BadgeError):
            data.reset_data(str(self.root.resolve()))
        self.assertEqual((outside / 'important.txt').read_text(), 'keep me')
        self.assertTrue((self.root / 'held-events/saved.txt').exists())

    def test_docker_reset_requires_bundled_image_marker_and_mounted_data(self):
        marker = self.directory / 'container-marker'
        with patch.object(data, 'data_root', return_value=Path('/data')), \
                patch.object(services, 'PROJECT_ROOT', Path('/app')), \
                patch.object(data, 'CONTAINER_DATA_MARKER', marker), \
                patch.object(Path, 'is_mount', lambda path: path == Path('/data')):
            with self.assertRaises(security.BadgeError):
                data.reset_root()
            marker.write_text('/unrelated\n')
            with self.assertRaises(security.BadgeError):
                data.reset_root()
            marker.write_text('/data\n')
            self.assertEqual(data.reset_root(), Path('/data'))
            with patch.object(Path, 'is_mount', return_value=False), self.assertRaises(security.BadgeError):
                data.reset_root()

    def test_unsupported_deletion_platform_fails_closed(self):
        saved = self.save('events/saved.txt')
        with patch.object(data.shutil.rmtree, 'avoids_symlink_attacks', False):
            self.assertEqual(self.request('/api/data/reset', {'confirmation': 'DELETE'})[0], 400)
        self.assertTrue(saved.exists())

    def test_operations_are_exclusive_through_the_end_of_streaming_responses(self):
        async def scenario(first_path, first_method, second_path, second_method):
            started, release = asyncio.Event(), asyncio.Event()

            async def application(scope, receive, send):
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                started.set()
                await release.wait()
                await send({'type': 'http.response.body', 'body': b'done'})

            middleware = data.DataManagementMiddleware(application)

            async def invoke(path, method):
                messages = []

                async def send(message):
                    messages.append(message)

                async def receive():
                    return {'type': 'http.request', 'body': b''}

                scope = {'type': 'http', 'path': path, 'method': method, 'scheme': 'http',
                         'headers': [(b'host', b'localhost'),
                                     (b'x-badge-data-token', data.SESSION_TOKEN.encode())]}
                await middleware(scope, receive, send)
                return messages[0]['status']

            first = asyncio.create_task(invoke(first_path, first_method))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertEqual(await invoke(second_path, second_method), 409)
            finally:
                release.set()
                await first
            self.assertEqual(await invoke(second_path, second_method), 200)
            self.assertEqual(middleware.active, 0)
            self.assertFalse(middleware.exclusive)

        for first, method, second, second_method in (
                ('/api/import', 'POST', '/api/data/reset', 'POST'),
                ('/api/data/backup/file', 'GET', '/api/data/reset', 'POST'),
                ('/api/data/backup', 'POST', '/api/templates', 'GET'),
                ('/api/data/reset', 'POST', '/api/import', 'POST')):
            with self.subTest(first=first, second=second):
                asyncio.run(scenario(first, method, second, second_method))


if __name__ == '__main__':
    unittest.main()
