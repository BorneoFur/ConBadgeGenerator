"""Stream multipart uploads to the project data disk and enforce request limits."""
import errno
from tempfile import SpooledTemporaryFile, TemporaryFile

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse

from app import security, services


def upload_directory():
    directory = security.asset_path(services.DATA_ROOT, ".uploads")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class DiskMultiPartParser(MultiPartParser):
    def on_headers_finished(self):
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is not None:
            # Replace the empty memory spool before data is written. The parent
            # parser retains ownership of error cleanup and file-size bookkeeping.
            disk_file = TemporaryFile(dir=upload_directory())
            previous = upload.file
            upload.file = disk_file
            self._files_to_close_on_error[-1] = disk_file
            previous.close()


class DiskUploadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def route(request):
            if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
                return await original(request)
            parser = DiskMultiPartParser(request.headers, request.stream())
            try:
                form = await parser.parse()
            except MultiPartException as exc:
                raise HTTPException(status_code=400, detail=exc.message) from exc
            # FastAPI reuses Request.form()'s cache, avoiding a second parse/copy.
            request._form = form
            try:
                return await original(request)
            finally:
                await form.close()
        return route


class UploadLimitsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        started = False

        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message = {**message, 'headers': [*message.get('headers', []),
                                                 (b'x-content-type-options', b'nosniff')]}
            await send(message)

        try:
            await self.handle(scope, receive, safe_send)
        except OSError as exc:
            if started or exc.errno not in (errno.ENOSPC, errno.EDQUOT):
                raise
            await JSONResponse({'detail': 'Not enough disk space for this upload or export. Free space in the project data directory and retry.'}, status_code=507)(scope, receive, safe_send)

    async def handle(self, scope, receive, send):
        if scope['method'] not in ('POST', 'PUT', 'PATCH'):
            await self.app(scope, receive, send)
            return
        content_type = dict(scope['headers']).get(b'content-type', b'').lower()
        limit = 2 * security.MIB if content_type.startswith(b'application/json') else security.MAX_REQUEST_BYTES
        detail = f'Request exceeds the {limit // security.MIB} MiB limit.'

        async def too_large():
            await JSONResponse({'detail': detail}, status_code=413)(scope, receive, send)

        length = dict(scope['headers']).get(b'content-length')
        if length is not None:
            try:
                if int(length) < 0:
                    raise ValueError
                if int(length) > limit:
                    await too_large()
                    return
            except ValueError:
                await JSONResponse({'detail': 'Invalid Content-Length.'}, status_code=400)(scope, receive, send)
                return

        if content_type.startswith(b'multipart/form-data'):
            total = 0

            async def limited_receive():
                nonlocal total
                message = await receive()
                total += len(message.get('body', b''))
                if total > limit:
                    # The multipart parser closes partial files on stream errors.
                    raise HTTPException(status_code=413, detail=detail)
                return message

            await self.app(scope, limited_receive, send)
            return

        # Bound other body types too, even for routes that do not consume a body.
        with SpooledTemporaryFile(max_size=security.MIB, dir=upload_directory()) as body:
            total = 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                total += len(chunk)
                if total > limit:
                    await too_large()
                    return
                await run_in_threadpool(body.write, chunk)
                if not message.get('more_body', False):
                    break
            await run_in_threadpool(body.seek, 0)
            finished = False

            async def replay():
                nonlocal finished
                if finished:
                    return await receive()
                chunk = await run_in_threadpool(body.read, 64 * 1024)
                finished = body.tell() == total
                return {'type': 'http.request', 'body': chunk, 'more_body': not finished}

            await self.app(scope, replay, send)
