"""One batch export at a time, with progress retained across browser refreshes.

Like data management and image_task, this local app uses one server worker.
Only small job summaries are kept in memory; downloads stream from disk.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
import uuid
from pathlib import Path

from app import services


_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_logger = logging.getLogger(__name__)


def active() -> dict | None:
    with _lock:
        return next((_summary(job) for job in _jobs.values() if job['status'] == 'running'), None)


def _summary(job: dict) -> dict:
    elapsed = (job.get('finished') or time.monotonic()) - job['started']
    completed, total = job['completed'], job['total']
    # Reserve one work unit for closing the archive / saving the PDF. Only a
    # successfully closed file reaches 100%; finalization has no reliable ETA.
    percent = 100 if job['status'] == 'completed' else math.floor(completed * 100 / (total + 1))
    remaining = None
    if job['status'] == 'running' and job['stage'] == 'rendering' and completed:
        render_elapsed = time.monotonic() - job['render_started']
        remaining = max(1, math.ceil(render_elapsed / completed * (total - completed + 1)))
    return {key: job[key] for key in ('id', 'event_id', 'mode', 'kind', 'status', 'stage',
                                      'completed', 'total', 'error', 'cancel_requested')} | {
        'percent': percent, 'elapsed_seconds': round(elapsed), 'remaining_seconds': remaining,
        'download_url': f"/api/export-jobs/{job['id']}/download" if job['status'] == 'completed' else None,
        'filename': job['output'].name if job.get('output') else None,
    }


def get(identifier: str) -> dict:
    with _lock:
        if identifier not in _jobs:
            raise services.BadgeError('Export task was not found. The server may have restarted; please export again.')
        return _summary(_jobs[identifier])


def cancel(identifier: str) -> dict:
    """Request a stop; the worker keeps its slot until file cleanup is finished."""
    with _lock:
        get(identifier)
        job = _jobs[identifier]
        if job['status'] == 'running':
            job.update(cancel_requested=True, stage='stopping')
        return _summary(job)


def download(identifier: str) -> Path:
    with _lock:
        summary = get(identifier)
        if summary['status'] != 'completed':
            raise services.BadgeError('This export is not ready to download.')
        output = _jobs[identifier]['output']
        if not output.is_file():
            raise services.BadgeError('The export file is no longer available. Please export again.')
        return output


def start(event_id: str, mode: str, kind: str, profile_bytes: bytes | None,
          identifier: str | None = None) -> dict:
    if mode not in ('rgb', 'cmyk') or kind not in ('jpeg', 'pdf'):
        raise services.BadgeError('Choose RGB or CMYK and ZIP or Master PDF.')
    if mode == 'cmyk' and profile_bytes is None:
        raise services.BadgeError('Choose a printer ICC / ICM profile first.')
    identifier = identifier or uuid.uuid4().hex
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise services.BadgeError('Invalid export task identifier.')
    with _lock:
        if identifier in _jobs:
            previous = _jobs[identifier]
            if (previous['event_id'], previous['mode'], previous['kind']) != (event_id, mode, kind):
                raise services.BadgeError('This export task identifier is already in use.')
            return get(identifier)
        if active():
            raise services.BadgeError('An export is already running. Wait for it to finish.')
        _, event = services.load_event(event_id)
        total = len(event['records'])
        if not total:
            raise services.BadgeError('There are no badges to export.')
        # Keep recent completed links available without an unbounded registry.
        while len(_jobs) >= 20:
            del _jobs[next(iter(_jobs))]
        job = {'id': identifier, 'event_id': event_id, 'mode': mode, 'kind': kind,
               'status': 'running', 'stage': 'preparing', 'completed': 0, 'total': total,
               'error': None, 'cancel_requested': False, 'started': time.monotonic()}
        _jobs[identifier] = job
        try:
            threading.Thread(target=_run, args=(job, profile_bytes if mode == 'cmyk' else None),
                             name=f'badge-export-{identifier}', daemon=True).start()
        except Exception:
            del _jobs[identifier]
            raise
        return _summary(job)


def _run(job: dict, profile_bytes: bytes | None) -> None:
    def progress(completed: int, total: int, stage: str) -> None:
        with _lock:
            job.update(completed=completed, total=total)
            if job['cancel_requested']:
                raise services.ExportCancelled('Export stopped.')
            if stage == 'rendering' and 'render_started' not in job:
                job['render_started'] = time.monotonic()
            job.update(completed=completed, total=total, stage=stage)

    try:
        progress(0, job['total'], 'preparing')
        render = services._export_master_pdf if job['kind'] == 'pdf' else services._export_jpeg_zip
        output = render(job['event_id'], profile_bytes, progress=progress)
        # A stop can arrive while closing the ZIP or saving the PDF. Resolve
    # stop vs completion atomically so cancelled files are never offered.
        with _lock:
            if job['cancel_requested']:
                output.unlink(missing_ok=True)
                raise services.ExportCancelled('Export stopped.')
            job.update(status='completed', stage='completed', output=output, finished=time.monotonic())
    except services.ExportCancelled:
        with _lock:
            job.update(status='cancelled', stage='cancelled', finished=time.monotonic())
    except Exception as exc:
        _logger.exception('Batch export failed')
        with _lock:
            job.update(status='failed', stage='failed', finished=time.monotonic(),
                       error=str(exc) if isinstance(exc, services.BadgeError) else
                       'Export failed. Check available disk space and the server log, then try again.')
