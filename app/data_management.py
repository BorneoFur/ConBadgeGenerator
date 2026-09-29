"""Back up and reset the configured data directory without parsing its contents."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from app import services, security, typography, export_jobs


SESSION_TOKEN = secrets.token_urlsafe(32)
_backups: dict[str, tuple[Path, str, float]] = {}
RESET_DIRECTORIES = frozenset({'templates', 'events', 'google-fonts', '.uploads', '.backups'})
STAGING_DIRECTORY = re.compile(r'\.staging-(?:template-set-)?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
CONTAINER_DATA_MARKER = Path('/etc/conbadge-data-root')


class DataManagementMiddleware:
    """Keep backup/reset exclusive with every API request, including file transfers.

    This local application must run with one worker. The counter covers the whole
    ASGI response, so reset cannot remove an export still being downloaded.
    """

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.active = 0
        self.exclusive = False
        self.batch_export = False

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith('/api/'):
            await self.app(scope, receive, send)
            return
        headers = dict(scope['headers'])
        if path.startswith('/api/data/'):
            if not self.trusted_request(scope, headers):
                await self.reject('Open this page through localhost to manage saved data.', 403, scope, receive, send)
                return
            if scope['method'] == 'POST' and not secrets.compare_digest(
                headers.get(b'x-badge-data-token', b''), SESSION_TOKEN.encode()
            ):
                await self.reject('Refresh the page before managing saved data.', 403, scope, receive, send)
                return
        exclusive = scope['method'] == 'POST' and path in ('/api/data/backup', '/api/data/reset')
        batch_export = scope['method'] == 'POST' and bool(
            re.fullmatch(r'/api/events/[^/]+/(?:export/[^/]+|export-jobs/[^/]+/[^/]+)', path))
        if exclusive:
            # Validate before the upload middleware creates its temporary directory.
            try:
                reset_root() if path == '/api/data/reset' else data_root()
            except security.BadgeError as exc:
                await self.reject(str(exc), 400, scope, receive, send)
                return
        with self.lock:
            exporting = export_jobs.active() is not None
            busy = (self.exclusive or (exclusive and (self.active > 0 or exporting))
                    or (batch_export and (self.batch_export or exporting)))
            if not busy:
                if batch_export:
                    self.batch_export = True
                if exclusive:
                    self.exclusive = True
                else:
                    self.active += 1
        if busy:
            await self.reject('Another operation or download is running. Wait for it to finish, then retry.',
                              409, scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self.lock:
                if batch_export:
                    self.batch_export = False
                if exclusive:
                    self.exclusive = False
                else:
                    self.active -= 1

    @staticmethod
    def trusted_request(scope, headers):
        try:
            target = urlsplit(scope.get('scheme', 'http') + '://' + headers.get(b'host', b'').decode('ascii'))
            if (target.hostname not in ('localhost', '127.0.0.1', '::1') or target.username is not None
                    or target.password is not None or target.path or target.query or target.fragment):
                return False
            target_port = target.port or (443 if target.scheme == 'https' else 80)
            if headers.get(b'sec-fetch-site', b'') not in (b'', b'none', b'same-origin'):
                return False
            if b'origin' in headers:
                origin = urlsplit(headers[b'origin'].decode('ascii'))
                origin_port = origin.port or (443 if origin.scheme == 'https' else 80)
                if (origin.scheme != target.scheme or origin.netloc.lower() != target.netloc.lower()
                        or origin_port != target_port or origin.path or origin.query or origin.fragment):
                    return False
            return True
        except (UnicodeError, ValueError):
            return False

    @staticmethod
    async def reject(detail, status, scope, receive, send):
        await JSONResponse({'detail': detail}, status_code=status,
                           headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})(scope, receive, send)


def data_root() -> Path:
    """Refuse reset/backup if configuration points at code or a broad system folder."""
    configured = services.DATA_ROOT
    if configured.is_symlink():
        raise security.BadgeError('The data directory must not be a symbolic link.')
    root = configured.resolve()
    project = services.PROJECT_ROOT.resolve()
    if (root == Path(root.anchor) or root == Path.home().resolve()
            or root == Path('/tmp').resolve()
            or root == Path(tempfile.gettempdir()).resolve() or project.is_relative_to(root)):
        raise security.BadgeError('Refusing to manage this directory. BADGE_DATA_DIR must be a dedicated data folder.')
    for name in ('app', 'tests', 'docs', '.git', '.venv', '.agents', '.codex'):
        protected = project / name
        if root.is_relative_to(protected) or protected.is_relative_to(root):
            raise security.BadgeError('The data directory must be separate from application files.')
    if root.exists() and not root.is_dir():
        raise security.BadgeError('The configured data path is not a directory.')
    return root


def unreadable_directory(error: OSError) -> None:
    raise security.BadgeError('A data folder could not be read. Check its permissions before continuing.') from error


def linux_mount_points() -> set[Path]:
    """Include Linux bind mounts, which Path.is_mount() may not recognize."""
    table = Path('/proc/self/mountinfo')
    if not table.exists():
        return set()
    try:
        return {Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), line.split()[4]))
                for line in table.read_text().splitlines()}
    except (OSError, IndexError) as exc:
        raise security.BadgeError('Cannot check mounted directories safely. Reset is unavailable.') from exc


def check_nested_mounts(root: Path) -> None:
    if any(mount != root and mount.is_relative_to(root) for mount in linux_mount_points()):
        raise security.BadgeError('The data directory contains another mounted directory. Remove that mount before continuing.')
    for parent, directories, _ in os.walk(root, followlinks=False, onerror=unreadable_directory):
        for name in directories:
            child = Path(parent) / name
            if not child.is_symlink() and child.is_mount():
                raise security.BadgeError('The data directory contains another mounted directory. Remove that mount before continuing.')


def reset_root() -> Path:
    """An environment variable alone must never authorize deleting arbitrary folders."""
    root = data_root()
    project = services.PROJECT_ROOT.resolve()
    local_root = project / '.badge_data'
    local = root == local_root and not root.is_mount() and root not in linux_mount_points()
    container = False
    if root == Path('/data') and project == Path('/app') and root.is_mount():
        try:
            # Installed outside the writable data volume by our Dockerfile.
            container = CONTAINER_DATA_MARKER.read_bytes() == b'/data\n'
        except OSError:
            pass
    if not (local or container):
        raise security.BadgeError("Browser reset is limited to this project's .badge_data folder or the bundled Docker /data volume. Custom BADGE_DATA_DIR folders cannot be reset here.")
    if (not shutil.rmtree.avoids_symlink_attacks or not hasattr(os, 'O_NOFOLLOW')
            or not hasattr(os, 'O_DIRECTORY') or os.unlink not in os.supports_dir_fd):
        raise security.BadgeError('This platform does not support safe directory deletion. Use the Docker version to reset data.')
    return root


def is_reset_entry(name: str) -> bool:
    return name in RESET_DIRECTORIES or STAGING_DIRECTORY.fullmatch(name) is not None


def reset_preview() -> dict:
    root = reset_root()
    names = sorted(child.name for child in root.iterdir()) if root.exists() else []
    return {'data_root': str(root),
            'delete_entries': [name for name in names if is_reset_entry(name)],
            'preserved_entries': [name for name in names if not is_reset_entry(name)]}


def cleanup_backup(identifier: str) -> None:
    entry = _backups.pop(identifier, None)
    if entry:
        shutil.rmtree(entry[0].parent, ignore_errors=True)


def exclude_from_backup(root: Path, parent: Path, name: str) -> bool:
    if parent == root:
        return name in ('.uploads', '.backups') or name.startswith('.staging-')
    # Imported attendee records and avatars are separate from template artwork
    # and generated exports. Exclude them without parsing possibly damaged JSON.
    return parent.parent == root / 'events' and name in ('event.json', 'assets')


def create_backup() -> dict[str, str]:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    check_nested_mounts(root)
    if (root / '.backups').is_symlink():
        raise security.BadgeError('The backup directory must not be a symbolic link.')
    for identifier in list(_backups):
        cleanup_backup(identifier)
    identifier = secrets.token_urlsafe(32)
    directory = security.asset_path(root, f'.backups/{identifier}')
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / 'backup.zip'
    filename = f"conbadge-backup-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.zip"
    skipped = []
    try:
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr('.badge_data/', b'')
            for parent, directories, files in os.walk(root, followlinks=False, onerror=unreadable_directory):
                base = Path(parent)
                directories[:] = [name for name in directories
                                  if not (base / name).is_symlink()
                                  and not exclude_from_backup(root, base, name)]
                for name in files:
                    if exclude_from_backup(root, base, name):
                        continue
                    source = base / name
                    relative = source.relative_to(root).as_posix()
                    try:
                        security.safe_relative_path(relative)
                        if '\\' in relative:
                            raise security.BadgeError('Ambiguous archive path.')
                    except security.BadgeError:
                        skipped.append(relative)
                        continue
                    if not stat.S_ISREG(source.lstat().st_mode):
                        skipped.append(relative)
                        continue
                    # Do not dereference a link substituted after the directory scan.
                    descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    with os.fdopen(descriptor, 'rb') as content:
                        if not stat.S_ISREG(os.fstat(content.fileno()).st_mode):
                            skipped.append(relative)
                            continue
                        with archive.open(f'.badge_data/{relative}', 'w', force_zip64=True) as output:
                            shutil.copyfileobj(content, output, length=1024 * 1024)
            archive.writestr('RESTORE.txt',
                             'Stop the app before restoring. Copy the contents of .badge_data/ into the app data directory.\n'
                             'For Docker, restore into the dedicated /data volume; see the project README.\n'
                             'Imported attendee lists (events/*/event.json) and avatars (events/*/assets/) are excluded.\n'
                             'Re-import the original attendee spreadsheet and avatar folder after restoring.\n'
                             'Generated badge images, PDFs, and ZIP exports are included.\n'
                             'Temporary uploads, unfinished staging folders, backup archives, and symbolic links are excluded.\n'
                             + ('Skipped files (unsafe names or non-regular files): ' + ', '.join(skipped) + '\n' if skipped else ''))
        _backups[identifier] = (path, filename, time.monotonic() + 900)
        return {'download_url': f'/api/data/backup/{identifier}', 'filename': filename}
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def get_backup(identifier: str) -> tuple[Path, str]:
    entry = _backups.get(identifier)
    if entry is None or entry[2] < time.monotonic():
        cleanup_backup(identifier)
        raise security.BadgeError('This backup download has expired. Create another backup.')
    return entry[0], entry[1]


def reset_data(expected_root: str) -> list[str]:
    root = reset_root()
    if expected_root != str(root):
        raise security.BadgeError('The data directory does not match your confirmation. Reopen the reset dialog.')
    # Anchor deletion to an open directory, and never follow a substituted symlink.
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise security.BadgeError('Cannot open the data directory safely. Nothing was deleted.') from exc
    try:
        if not os.path.samestat(os.fstat(descriptor), root.stat(follow_symlinks=False)):
            raise security.BadgeError('The data directory changed. Nothing was deleted; reopen the reset dialog.')
        check_nested_mounts(root)
        with os.scandir(descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            if not is_reset_entry(name):
                continue
            if stat.S_ISDIR(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                shutil.rmtree(name, dir_fd=descriptor)
            else:
                os.unlink(name, dir_fd=descriptor)
        _backups.clear()
        typography.clear_font_cache()
        return [name for name in names if not is_reset_entry(name)]
    except OSError as exc:
        raise security.BadgeError('Reset could not finish. Some data may already be deleted. Check disk permissions and retry.') from exc
    finally:
        os.close(descriptor)
