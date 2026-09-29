# HTTP entry points: browser actions in static/app.js call these routes.
# Keep request/file parsing here; template storage and rendering live in services.py.
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app import services, google_fonts, security, data_management, export_jobs
from app.http_security import UploadLimitsMiddleware, DiskUploadRoute


app = FastAPI(title="Convention Badge Generator", docs_url=None, redoc_url=None)
app.add_middleware(UploadLimitsMiddleware)
app.add_middleware(data_management.DataManagementMiddleware)
app.router.route_class = DiskUploadRoute
STATIC_ROOT = Path(__file__).resolve().parent / "static"


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


async def read_upload(upload: UploadFile, limit: int) -> bytes:
    if upload.size is not None and upload.size > limit:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit // security.MIB} MiB limit.")
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit // security.MIB} MiB limit.")
    return content


async def image_upload(upload: UploadFile, limit: int):
    size = await run_in_threadpool(security.source_size, upload.file)
    if size > limit:
        raise HTTPException(status_code=413, detail=f"Image exceeds the {limit // security.MIB} MiB limit.")
    await upload.seek(0)
    return upload.file


# Tier Backgrounds: list the active template for each Tier.
@app.get("/api/templates")
def templates() -> list[dict]:
    try:
        installed = services.available_templates(services.discover_templates())
        result = []
        tiers = {item["manifest"]["name"] for item in installed.values()}
        for tier in sorted(tiers, key=str.casefold):
            item = services.template_for_tier(tier, installed)
            if not item:
                continue
            manifest = item["manifest"]
            result.append({
                **{key: manifest[key] for key in ("id", "name", "version", "profile_picture", "print")},
                "background_asset": manifest.get("background", {}).get("asset"),
                "custom": item["root"].is_relative_to(services.DATA_ROOT / "templates"),
            })
        return result
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/templates/create")
async def create_template(
    tier: str = Form(...),
    artwork: UploadFile = File(...),
    avatar_enabled: bool = Form(False),
) -> dict:
    try:
        return await run_in_threadpool(services.create_tier_template, tier, artwork.filename or "",
                                       await image_upload(artwork, security.MAX_BACKGROUND_BYTES), avatar_enabled)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/templates/{template_id}/background")
async def replace_template_background(template_id: str, artwork: UploadFile = File(...)) -> dict:
    try:
        return await run_in_threadpool(services.update_template_background, template_id, artwork.filename or "",
                                       await image_upload(artwork, security.MAX_BACKGROUND_BYTES))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Edit layout: load the manifest and attendee samples for the editor.
@app.get("/api/templates/{template_id}/editor")
def template_editor(template_id: str, event_id: str | None = Query(None)) -> dict:
    try:
        return services.editor_template(template_id, event_id)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Edit layout text preview: render unsaved text settings without saving the layout.
@app.post("/api/templates/{template_id}/text-preview")
def template_text_preview(template_id: str, payload: dict = Body(...)) -> Response:
    try:
        return Response(services.editor_text_preview(template_id, payload), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get("/api/templates/{template_id}/background-preview")
def template_background_preview(template_id: str):
    try:
        path = services.template_background_path(template_id)
        if not path:
            raise HTTPException(status_code=404, detail="This starter template has no background image yet.")
        return Response(security.image_preview(path), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get("/api/templates/{template_id}/assets/{asset_path:path}")
def template_asset_preview(template_id: str, asset_path: str):
    try:
        path = services.template_asset_path(template_id, asset_path)
        if path.suffix.lower() in security.IMAGE_EXTENSIONS:
            return Response(security.image_preview(path), media_type="image/png")
        return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                            headers={"Content-Security-Policy": "sandbox; default-src 'none'"})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Editor masks use normalized alpha, matching grayscale/transparent masks in badge output.
@app.get("/api/templates/{template_id}/mask-preview/{asset_path:path}")
def template_mask_preview(template_id: str, asset_path: str) -> Response:
    try:
        return Response(services.template_mask_preview(template_id, asset_path), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/templates/{template_id}/assets")
async def upload_template_asset(template_id: str, kind: str = Form(...), asset: UploadFile = File(...)) -> dict:
    try:
        return await run_in_threadpool(services.add_template_asset, template_id, kind, asset.filename or "", await read_upload(asset, security.MAX_IMAGE_BYTES))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/templates/{template_id}/fonts")
async def upload_template_font(
    template_id: str,
    family_name: str = Form(...),
    style: str = Form(...),
    font_file: UploadFile = File(...),
) -> dict:
    try:
        return services.add_template_font(template_id, family_name, style, font_file.filename or "", await read_upload(font_file, security.MAX_FONT_BYTES))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# The catalog is local; font files download only when selected or needed for fallback.
@app.get("/api/google-fonts")
def available_google_fonts() -> list[dict[str, str]]:
    return google_fonts.CATALOG


@app.post("/api/templates/{template_id}/google-fonts")
def use_google_font(template_id: str, payload: dict = Body(...)) -> dict:
    try:
        return services.add_google_font(template_id, str(payload.get("font_id", "")), str(payload.get("style", "regular")))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Save layout / Ctrl or Cmd+S: persist the editor elements and font definitions.
@app.put("/api/templates/{template_id}/layout")
def save_template_editor(template_id: str, payload: dict = Body(...)) -> dict:
    try:
        return services.save_template_layout(template_id, payload.get("elements", []), payload.get("fonts"))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.put("/api/templates/{template_id}/settings")
def save_template_settings(template_id: str, payload: dict = Body(...)) -> dict:
    try:
        return services.update_template_settings(template_id, str(payload.get("name", "")), bool(payload.get("profile_picture")))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: str) -> dict:
    try:
        services.delete_custom_template(template_id)
        return {"deleted": template_id}
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get("/api/template-formats/export")
def download_template_formats() -> Response:
    try:
        payload = services.template_set_json()
        return Response(payload, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="conbadge-template-formats.json"'})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Import event data: normalize CSV/XLSX and pass optional avatar files to the shared importer.
@app.post("/api/import")
async def import_event(csv_file: UploadFile = File(...), avatar_files: list[UploadFile] = File(default=[])) -> dict:
    if not (csv_file.filename or "").lower().endswith((".csv", ".xlsx")):
        raise HTTPException(status_code=400, detail="Choose a CSV or XLSX file.")
    try:
        supplied: list[tuple[str, security.ImageSource]] = []
        if len(avatar_files) > security.MAX_ASSETS:
            raise HTTPException(status_code=413, detail="Too many avatar files.")
        total_bytes = 0
        for uploaded in avatar_files:
            name = uploaded.filename or ""
            relative = security.safe_relative_path(name)
            if relative.suffix.lower() not in security.IMAGE_EXTENSIONS:
                continue
            content = await image_upload(uploaded, security.MAX_IMAGE_BYTES)
            total_bytes += await run_in_threadpool(security.source_size, content)
            if total_bytes > security.MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Avatar upload exceeds the 4 GiB limit.")
            supplied.append((name, content))
        csv_bytes = services.attendee_file_to_csv(csv_file.filename or "", await read_upload(csv_file, security.MAX_SPREADSHEET_BYTES))
        return await run_in_threadpool(services.create_event, csv_bytes, supplied)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Refresh missing-avatar warnings after a Tier setting or layout changes.
@app.get("/api/events/{event_id}/summary")
def imported_event_summary(event_id: str) -> Response:
    try:
        _, event = services.load_event(event_id)
        return JSONResponse(services.event_summary(event), headers={"Cache-Control": "no-store"})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# View a badge: render the complete saved badge, including background and every visible layer.
@app.get("/api/events/{event_id}/records/{ticket_id}/preview")
def badge_preview(event_id: str, ticket_id: str) -> Response:
    try:
        return Response(services.preview(event_id, ticket_id), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get("/api/events/{event_id}/records/{ticket_id}/avatar")
def editor_avatar_preview(event_id: str, ticket_id: str) -> Response:
    try:
        path = services.event_avatar_preview(event_id, ticket_id)
        return Response(security.image_preview(path, max_pixels=security.MAX_IMAGE_PIXELS), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get("/api/barcode-preview")
@app.get("/api/qr-preview")
def editor_qr_preview(value: str = Query("Preview"), foreground: str = Query("#000000"), background: str = Query("#ffffff"), quiet_zone_modules: int = Query(4), symbology: str = Query("qr"), error_correction: str = Query("M")) -> Response:
    try:
        return Response(services.qr_preview(value, foreground, background, quiet_zone_modules, symbology, error_correction), media_type="image/png")
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Preview download buttons export one badge; the routes below export the whole event.
@app.post("/api/events/{event_id}/badge-export/{kind}")
async def single_badge_export(event_id: str, kind: str, ticket_id: str = Form(...), icc_profile: UploadFile | None = File(None)) -> Response:
    try:
        profile_bytes = None
        if icc_profile is not None:
            if not (icc_profile.filename or "").lower().endswith((".icc", ".icm")):
                raise services.BadgeError("Choose an ICC or ICM profile for CMYK export.")
            profile_bytes = await read_upload(icc_profile, security.MAX_PROFILE_BYTES)
        content, filename = await run_in_threadpool(services.export_badge, event_id, ticket_id, kind, profile_bytes)
        return Response(content, media_type="image/jpeg" if kind == "jpg" else "application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/events/{event_id}/export-jobs/{mode}/{kind}")
async def start_export_job(event_id: str, mode: str, kind: str,
                           icc_profile: UploadFile | None = File(None),
                           request_id: str | None = Form(None)) -> Response:
    try:
        profile = None
        if mode == 'cmyk' and icc_profile is not None:
            if not (icc_profile.filename or '').lower().endswith(('.icc', '.icm')):
                raise services.BadgeError('Choose an ICC or ICM profile for CMYK export.')
            profile = await read_upload(icc_profile, security.MAX_PROFILE_BYTES)
        job = await run_in_threadpool(export_jobs.start, event_id, mode, kind, profile, request_id)
        return JSONResponse(job, status_code=202, headers={'Cache-Control': 'no-store'})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get('/api/export-jobs/active')
async def active_export_job() -> Response:
    return JSONResponse({'job': export_jobs.active()}, headers={'Cache-Control': 'no-store'})


@app.get('/api/export-jobs/{identifier}')
async def export_job_status(identifier: str) -> Response:
    try:
        return JSONResponse(export_jobs.get(identifier), headers={'Cache-Control': 'no-store'})
    except services.BadgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/api/export-jobs/{identifier}/cancel')
async def cancel_export_job(identifier: str) -> Response:
    try:
        return JSONResponse(export_jobs.cancel(identifier), headers={'Cache-Control': 'no-store'})
    except services.BadgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get('/api/export-jobs/{identifier}/download')
def download_export_job(identifier: str) -> FileResponse:
    try:
        output = export_jobs.download(identifier)
        return FileResponse(output, filename=output.name, headers={'Cache-Control': 'no-store'},
                            media_type='application/zip' if output.suffix == '.zip' else 'application/pdf')
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/events/{event_id}/export/rgb-jpeg")
def rgb_jpeg_export(event_id: str) -> FileResponse:
    try:
        output = services.export_rgb_jpeg_zip(event_id)
        return FileResponse(output, media_type="application/zip", filename=output.name)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/events/{event_id}/export/rgb-pdf")
def rgb_pdf_export(event_id: str) -> FileResponse:
    try:
        output = services.export_rgb_master_pdf(event_id)
        return FileResponse(output, media_type="application/pdf", filename=output.name)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/events/{event_id}/export/cmyk-jpeg")
async def cmyk_jpeg_export(event_id: str, icc_profile: UploadFile = File(...)) -> FileResponse:
    try:
        output = await run_in_threadpool(services.export_cmyk_jpeg_zip, event_id, icc_profile.filename or "", await read_upload(icc_profile, security.MAX_PROFILE_BYTES))
        return FileResponse(output, media_type="application/zip", filename=output.name)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/events/{event_id}/export/cmyk-pdf")
async def cmyk_pdf_export(event_id: str, icc_profile: UploadFile = File(...)) -> FileResponse:
    try:
        output = await run_in_threadpool(services.export_cmyk_master_pdf, event_id, icc_profile.filename or "", await read_upload(icc_profile, security.MAX_PROFILE_BYTES))
        return FileResponse(output, media_type="application/pdf", filename=output.name)
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post("/api/template-formats/import")
async def upload_template_formats(format_file: UploadFile = File(...)) -> dict:
    if not (format_file.filename or "").lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Choose a template format JSON file.")
    try:
        return await run_in_threadpool(services.import_template_set_json, await read_upload(format_file, security.MAX_TEMPLATE_BYTES))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get('/api/data/session')
def data_management_session() -> Response:
    return JSONResponse({'token': data_management.SESSION_TOKEN}, headers={'Cache-Control': 'no-store'})


@app.post('/api/data/backup')
def prepare_data_backup() -> dict:
    try:
        return data_management.create_backup()
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get('/api/data/backup/{identifier}')
def download_data_backup(identifier: str) -> FileResponse:
    try:
        path, filename = data_management.get_backup(identifier)
        return FileResponse(path, media_type='application/zip', filename=filename,
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'},
                            background=BackgroundTask(data_management.cleanup_backup, identifier))
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.get('/api/data/reset-preview')
def preview_saved_data_reset() -> Response:
    try:
        return JSONResponse(data_management.reset_preview(), headers={'Cache-Control': 'no-store'})
    except services.BadgeError as exc:
        raise api_error(exc) from exc


@app.post('/api/data/reset')
def reset_saved_data(payload: dict = Body(...)) -> dict:
    if payload.get('confirmation') != 'DELETE':
        raise HTTPException(status_code=400, detail='Type DELETE to confirm deleting all saved layouts and event data.')
    try:
        preserved = data_management.reset_data(payload.get('data_root'))
        return {'reset': True, 'preserved_entries': preserved}
    except services.BadgeError as exc:
        raise api_error(exc) from exc


# Serve index.html, app.js, styles.css, and examples after registering all API routes.
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
