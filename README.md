# Convention Badge Generator

Local-first, template-driven badge generation for conventions.

Read [How Badge Generation Works](docs/how-it-works.md) for the flow from attendee
import to PNG masters, JPEG/PDF exports, downloads, and stopping an export.

For development, start with the [code guide](docs/code-guide.md) to locate the editor,
core renderer, import pipeline, and export code.

## What the first implementation provides

- English browser UI served only from the local machine.
- CSV / XLSX import with an optional avatar folder; images are matched automatically by Ticket ID.
- One-image-per-Tier template creation for JPG, JPEG, and PNG background artwork.
- A visual Tier editor with draggable layers, resizing, rotation, snapping, undo/redo, artwork overlays, Avatar masks, and text/barcode controls.
- One self-contained JSON file for saving and restoring all active Tier formats, including artwork, masks, and uploaded fonts.
- ZIP backups of saved layouts, artwork, fonts, and generated exports, plus a confirmed reset from the browser. Imported attendee lists and avatars are excluded from backups.
- RGB master rendering, QR Code / Data Matrix / Aztec generation, and RGB JPEG + PDF ZIP or master-PDF export without an ICC upload. Optional CMYK export uses a user-supplied ICC profile.

The renderer deliberately treats `ticket_id` and `tier` as data. It does not infer meaning from ID prefixes or hard-code tier names.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` in a browser.

## Run with Docker

Install and start Docker Desktop on macOS/Windows, or Docker Engine with the Compose
plugin on Linux. From the project directory, run:

```sh
docker compose up --build -d
```

Open `http://127.0.0.1:8000`. If a local copy of the app already uses port 8000,
choose another host port with `BADGE_PORT=8001 docker compose up --build -d` and
open `http://127.0.0.1:8001`.

```sh
docker compose ps
docker compose logs -f badge
docker compose down
```

`down` stops and removes the container while retaining saved data. The app stores
templates, imported attendees, avatars, exports, and downloaded fonts in the named
Docker volume `conbadgegenerator_badge-data`, mounted at `/data`. Existing local
`.badge_data` files are not copied into the image. To bring over Tier layouts,
download the formats JSON from the local app and load it in the Docker app, then
import the attendee spreadsheet and avatar folder through the browser.
`docker compose down --volumes` deletes this volume and its saved data.

### Container isolation

[compose.yaml](compose.yaml) applies these settings:

- A non-root user (UID/GID 10001), a read-only root filesystem, all Linux
  capabilities dropped, and `no-new-privileges` enabled.
- A dedicated writable data volume and a 64 MiB temporary filesystem mounted with
  `noexec`, `nosuid`, and `nodev`. The project directory, home directory, and Docker
  socket are not mounted into the running container.
- Port 8000 published only on the host's `127.0.0.1`. The app listens on `0.0.0.0`
  inside the container so Docker can forward that local port.
- Limits of 2 CPUs, 2 GiB RAM with no additional swap, and 128 processes/threads.
  Uvicorn runs one worker with at most 8 concurrent connections/tasks. Logs rotate,
  and the container stays stopped after a crash until restarted.

These options follow the [Docker Compose service reference](https://docs.docker.com/reference/compose-file/services/).
Use Compose to start the app; a plain `docker run` does not apply the restrictions
in `compose.yaml`. Keep Docker updated and retain its default seccomp profile.

Outbound network access remains enabled for Google Fonts and automatic multilingual
fallback. This setup limits host access but is not a complete RCE sandbox: a compromised
app can still access its data volume and network. The new data-management endpoints
check Host/Origin and require a session token for changes; other endpoints still lack
these checks. The data volume has no configured disk quota; uploads
and exports consume Docker's available disk space. For Docker Desktop, set its overall
disk usage limit as well as the resource limits here.

Adjust `cpus`, `mem_limit`, `memswap_limit`, and `pids_limit` in `compose.yaml` if needed.
Keep `memswap_limit` equal to `mem_limit` to allow no additional swap. Very large badge
canvases may exceed the default 2 GiB memory budget and stop the container; file upload
limits and container memory limits are separate settings.

After changing application code or requirements, rebuild the image. To also refresh
the Python base image and installed operating system packages:

```sh
docker compose build --pull --no-cache
docker compose up -d
```

The image installs the dependency versions from `requirements.txt`, including Pillow
12.3.0, independently of the host's Python environment. To check the container version:

```sh
docker compose exec badge python -c 'import PIL; print(PIL.__version__)'
```

## Back up or reset saved data

At the bottom of the page, **Back up or reset saved data** provides:

- **Download backup ZIP**: saves custom layouts, backgrounds, artwork, masks,
  generated exports, and downloaded/uploaded fonts. Imported attendee lists
  (`events/*/event.json`) and avatars (`events/*/assets/`) are excluded. Save any
  open layout changes first. The backup reads stored files directly, so damaged layout
  JSON does not prevent a backup. Temporary uploads, unfinished staging folders,
  previous backup archives, symbolic links, and unsafe file names are excluded.
  The ZIP contains a `.badge_data/` folder and `RESTORE.txt`.
- **Reset all saved data…**: opens a warning that **all saved layouts and other data
  will be permanently deleted**. You can download a backup in this dialog or cancel.
  Type exactly `DELETE` to enable the final deletion button. Wait until your browser
  has finished saving the backup before confirming. The page reloads after reset;
  built-in starter Tiers remain available.

Reset still deletes imported attendee lists and avatars. Keep your original spreadsheet
and avatar folder so you can re-import them after restoring; the ZIP cannot restore
these inputs. Generated badge images, PDFs, and ZIP exports remain in the backup and
can contain attendee names and pictures.

The reset dialog shows the exact directory, the app folders to delete, and other
entries that will be kept. Browser reset is permitted **only** for this project's
`.badge_data` directory or the dedicated `/data` volume in the supplied Docker image.
Other `BADGE_DATA_DIR` paths can be backed up but cannot be reset through the page;
setting this variable never grants deletion permission for an arbitrary folder.

Within an allowed directory, reset only removes `templates`, `events`, `google-fonts`,
`.uploads`, `.backups`, and staging folders matching the app's UUID naming format.
Other files and folders at the data-directory root are preserved. Everything **inside**
the app folders is deleted, so do not put unrelated personal files there. The data
directory/mount itself and application files remain. Symlinks are not followed, nested
mounts are rejected, and local `.badge_data` must not itself be a mount. Safe deletion
requires directory-descriptor support (macOS/Linux); use Docker on other platforms.
Keep the supplied Compose named-volume configuration rather than mounting a personal
folder at `/data`.

The backup ZIP needs additional
space on the data disk while being prepared and downloaded; it is removed after the
download response finishes. A download link expires after 15 minutes. If the server
restarts or a transfer fails, create a new backup; abandoned files may remain under
`.backups` until reset.

Backup preparation and reset reject requests while another API operation or download
is active. Wait for that operation to finish, then retry. Run **one Uvicorn worker and
one app instance per data directory** for this protection; the Docker command already
does this. Open the page through `localhost` or `127.0.0.1` to use these controls.

### Restore a backup ZIP

The ZIP is restored manually. **Load formats JSON** under Tier Backgrounds still
restores the separate layout-only JSON format; it does not accept backup ZIPs.

1. Stop the app and extract your backup into a separate folder. Enable hidden-file
   display if your file manager hides `.badge_data`.
2. For a local run, replace the contents of the configured data directory with the
   extracted `.badge_data/` contents, then restart the app. Keep the existing data
   somewhere safe if you may need it.
3. For Docker, first reset saved app data through the page to clear the app folders,
   then stop the container and copy your extracted backup into its volume:

   ```sh
   docker compose stop badge
   tar -C /path/to/extracted/.badge_data -cf - . | docker compose run --rm -T --no-deps badge tar --no-same-owner -xf - -C /data
   docker compose up -d
   ```

4. Re-import the attendee spreadsheet and avatar folder. They are not included in the
   backup ZIP. Previously generated exports can be found under the restored
   `events/<old-event-id>/exports` and `masters` folders.

Use your own trusted backup. Restoring a backup with damaged files also restores those
files; when troubleshooting, you can instead reset and import fresh layouts/attendees.

## Image safety and upload limits

Avatar, background, artwork, and mask uploads must be actual, single-frame PNG or
JPEG images with a matching `.png`, `.jpg`, or `.jpeg` extension. The app restricts
decoding to these formats, fully decodes each upload, and saves a fresh image.
PNG transparency is preserved, EXIF orientation is applied, and background DPI is
retained within the supported 72–1200 range (otherwise 300). Other metadata and
trailing content are removed. JPEG uploads are re-encoded at quality 95.

Previously saved images are also checked when previewing or rendering; avatars
retain their 25-million-pixel limit. Rejected avatars identify the filename in the
import error; replace or resize that image and import again. Non-image files in
avatar folders are ignored.

| Resource | Limit |
| --- | --- |
| Avatar, artwork, or mask upload | 32 MiB per file; 25 million pixels |
| Background upload | 2 GiB per file; 100 million pixels |
| Stored image / image decoded from a template JSON | 2 GiB per image; 100 million pixels, with the 25-million-pixel limit for saved avatars |
| Image or rendered layer dimensions | 32,768 pixels per side, in addition to the applicable total pixel limit |
| Whole upload request / normalized avatars per import | 4 GiB each; the request limit includes all files and multipart overhead |
| JSON API request body, such as Save layout | 2 MiB; template JSON files use multipart upload and the separate template limit below |
| Multipart files per request | 1,000, including the attendee spreadsheet (at most 999 avatar files with one spreadsheet) |
| Template JSON / total normalized assets | 128 MiB each; 100 Tiers and 1,000 assets |
| Font / ICC profile | 64 MiB / 4 MiB |
| UTF-8 TXT license asset | 1 MiB |
| CSV or XLSX | 16 MiB, up to 10,000 attendee rows |
| XLSX contents | 64 MiB uncompressed; up to 1,000 ZIP entries and 64 columns |
| Badge canvas or individual rendered layer | 100 million pixels |
| Template layer budget | 400 million pixels for the canvas plus all layer areas; at most 100 layers |
| Editor text preview | 16 million pixels |
| Background, avatar, and artwork resource previews | Fit within 2,048 × 2,048 pixels |

All applicable limits must be satisfied. Template JSON includes base64-encoded assets,
so the final file is larger than the asset bytes alone. A background that can be uploaded
may still be too large to include in the 128 MiB template export.

Requests declaring an oversized Content-Length are rejected before multipart parsing.
The app also counts bytes while receiving the body and rejects it when the limit is
crossed, including when Content-Length is missing or understates the size. Multipart
files stream to temporary files under `.badge_data/.uploads` (or
`BADGE_DATA_DIR/.uploads`). Background and avatar processing reads these files directly.
Image processing tasks run one at a time within each server process to limit concurrent
pixel allocations; a batch export can make other image requests wait. Disk space or
quota exhaustion returns HTTP 507 when the response has not started.

Template packages accept only PNG/JPEG, TTF/OTF, and UTF-8 TXT license assets. Font IDs
and asset paths are checked before writes. Resource routes generate PNG image previews
and download font/license files as attachments, with content sniffing disabled.

Keep Pillow and its bundled image libraries updated. These checks reduce the
attack surface; image decoding still runs in the application process. The Docker
configuration above adds isolation around that process. For direct local runs, bind
the app to localhost; with Docker, keep the published host port bound to localhost.

### Changing upload limits

Most limits are constants near the top of [app/security.py](app/security.py).
`MIB` means 1,048,576 bytes; `GIB` means 1,024 MiB. Edit the relevant constants and
restart the server, or let Uvicorn reload them when running with `--reload`.

| What to change | Constant in `app/security.py` | Current value |
| --- | --- | --- |
| Individual background file | `MAX_BACKGROUND_BYTES` | `2 * GIB` |
| Individual avatar, artwork, or mask file | `MAX_IMAGE_BYTES` | `32 * MIB` |
| Stored image size accepted by the decoder and normalization output | `MAX_STORED_IMAGE_BYTES` | Follows `MAX_BACKGROUND_BYTES` |
| Whole upload request and total avatar bytes per import | `MAX_REQUEST_BYTES` | `4 * GIB` |
| Template JSON file and total normalized template assets | `MAX_TEMPLATE_BYTES` | `128 * MIB` |
| Individual font / ICC profile / spreadsheet file | `MAX_FONT_BYTES` / `MAX_PROFILE_BYTES` / `MAX_SPREADSHEET_BYTES` | `128 * MIB` / `256 * MIB` / `64 * MIB` |

For example, allowing a background file up to 4 GiB requires
`MAX_BACKGROUND_BYTES = 4 * GIB` and a larger request allowance, such as
`MAX_REQUEST_BYTES = 8 * GIB`, to leave room for form fields and multipart overhead.
`MAX_STORED_IMAGE_BYTES` follows the background limit through its existing assignment.
Raise `MAX_TEMPLATE_BYTES` too if the resulting template must fit in a larger JSON
export, allowing for base64 and JSON overhead.

File size and pixel limits are independent. For larger image dimensions, review
`MAX_IMAGE_PIXELS` (avatars, artwork, and masks), `MAX_BACKGROUND_PIXELS` (backgrounds
and other stored images), `MAX_RENDER_PIXELS`, `MAX_LAYER_PIXELS`, and `MAX_SIDE`.
The editor text preview also has a separate 16-million-pixel check in
`editor_text_preview()` in [app/services.py](app/services.py).

Additional limits are defined outside those byte constants:

- The 2 MiB `application/json` request limit is in `UploadLimitsMiddleware.handle()`
  in [app/http_security.py](app/http_security.py). It applies to layout API requests;
  template JSON file uploads use multipart instead.
- To allow more uploaded files, set `max_files` when constructing `DiskMultiPartParser`
  in `DiskUploadRoute`, and adjust `MAX_ASSETS` in `app/security.py` as needed. The
  parser currently uses Starlette's default of 1,000 files, including the spreadsheet.
- XLSX ZIP entry count, uncompressed size, and column limits are in
  `attendee_file_to_csv()` in `app/services.py`; the row limit uses `MAX_RECORDS`.

After changing a limit, update the tables above and any fixed numbers in error messages
in `app/security.py`, `app/main.py`, and `app/services.py` so the messages match the
new settings. Font downloads also have a separate 64 MiB cap in `app/google_fonts.py`.

## Import package

Click the **i** button next to **Import event data** for instructions and an example download.
You can also use **Download example CSV** or **Download example XLSX** directly below that heading,
or open [the example CSV](app/static/example-attendees.csv) /
[the example XLSX](app/static/example-attendees.xlsx) from this repository.

1. Download the example and replace its rows with your attendee data. Keep the headers.
2. Save as **CSV UTF-8 (comma-delimited)** or **Excel (.xlsx)**. Keep ticket IDs and barcode content as text to preserve leading zeros and long numbers.
3. Make sure each `tier` matches a Tier in **Tier Backgrounds**; create any new Tiers before importing.
4. Choose **Attendee CSV / XLSX** and, optionally, an **Avatar folder**, then click **Import event**.
5. Check the result under **View a badge**. Under **Export for print**, download directly in the default **RGB** mode. Choose **CMYK** only when you have a printer ICC / ICM profile.

For XLSX, put the column headers in row 1 of the **first worksheet**; only that worksheet is read.
The server converts it to UTF-8 CSV in memory, then uses the same validation, avatar matching,
and badge import process as CSV uploads. No manual conversion is needed. Use plain values:
formula and error cells are rejected with their cell address; copy and paste as values first.
Excel display formatting is not applied during conversion, so set ID and barcode cells to **Text**
before entering values. Legacy `.xls` files are not supported; save them as `.xlsx` first.

Google Sheets users can choose **File → Download → Microsoft Excel (.xlsx)** and upload that file.

```csv
number,ticket_id,display_name,tier,qr_token
1,A-001,Bob,Attendees,example-token-1
2,S-002,Alice,Sponsors,example-token-2
3,SS-003,Charlie,Super Sponsors,example-token-3
```

| Column | Required | Meaning |
| --- | --- | --- |
| `number` | No | Row number; filled automatically if omitted or empty. |
| `ticket_id` | Yes | Unique ticket ID for each attendee. |
| `display_name` | Yes | Name printed on the badge. |
| `tier` | Yes | Matching Tier name, such as `Sponsors`. |
| `qr_token` | Yes | Content encoded in QR Code, Data Matrix, or Aztec; keep this header for all types. |

Example tokens are placeholders. Replace them with your own values; the app does not create or
validate ticket tokens. The sample uses the three bundled Tiers and can be imported without avatars.

Do not include image paths in the CSV. An avatar is matched by its ticket ID filename:
`S-002.png`, `S-002.jpg`, or `S-002.jpeg` matches `S-002`. Keep only one matching image per ticket ID.
Missing images leave the avatar area transparent, and avatars only appear on Tiers with avatars enabled.

After import, **Missing avatars** under **Export for print** lists the Ticket ID,
display name, and Tier of each badge that needs an avatar but has no matching image.
Download the CSV list to track follow-ups. The list updates when you save Tier or
layout changes; Tiers with avatars disabled do not trigger a warning. Add the missing
images to your original avatar folder and re-import it with the spreadsheet to clear
the resolved warnings. Preview and export remain available while avatars are missing.

## Templates

Use **Tier Backgrounds** in the browser. Each card represents one Tier; select a JPG/JPEG/PNG
inside that card and upload it. The app updates only that Tier's Template while retaining its
existing Avatar, name, Ticket ID, and QR layout. It derives the physical canvas from embedded image
PPI when available or 300 PPI otherwise, and proportionally carries the existing layout across.
Use **Download all formats JSON** to save every active Tier in one self-contained JSON document.
Loading that document restores its Tiers and their assets without deleting unrelated local Tiers.

Renaming a built-in Tier replaces its starter card: renaming `Attendees` to
`Attendees-A` shows only `Attendees-A`, including after refresh or a formats JSON
round trip. Layout, background, and font edits retain this association. Deleting the
custom replacement restores its built-in starter; resetting saved data restores all
starters. After renaming a Tier, update the spreadsheet's `tier` values before importing.

The internal manifest format is documented in `docs/template-manifest.md`.

In **Edit layout → Fonts**, select a Google font or paste a family link from
[Google Fonts](https://fonts.google.com/), choose a style, and click **Download & use font**.
Automatic downloads support OFL-licensed families from Google's official repository.
Original font files, source details, and OFL licenses are included in template exports.
Download each static style you need; variable weight fonts also support Regular/Bold
through their weight axis. Italic requires an italic font file when available.

**Automatic — multilingual Noto** fills missing characters using Noto Sans plus its
SC, TC, JP and KR families. First use requires internet; downloads are cached under
`.badge_data/google-fonts` (or `BADGE_DATA_DIR/google-fonts`). A new computer downloads
fallback fonts as needed; these shared cache files are not part of template exports.
Choose **Language / Han glyphs** to set the fallback's preferred regional Han forms;
pure Han text cannot reliably identify its language. A selected font takes priority.
Unknown characters produce a clear error instead of silent missing-glyph boxes.
This cannot repair text that was already incorrectly decoded before import.

Expand **Upload a font file instead** to add TTF/OTF files. Uploaded families are
selectable under **Properties → Font family**, with the same automatic fallback.
Uploaded font files also travel in template exports: only share files whose license
allows it. Selecting a font does not grant redistribution rights. **Save layout**
keeps your font assignment. The editor and final output share Unicode shaping,
combining-accent handling, wrapping and automatic font sizing.

All dimensions in a manifest are in millimetres. The renderer calculates its raster canvas from
the declared target PPI. A background's pixel dimensions do not define physical print size.

The **Open preview** button opens the selected badge with separate **Download JPG** and **Download PDF** buttons. Selecting an ICC file needs no separate upload button: it is uploaded and applied when a CMYK download is requested. Inside the preview, choose **sRGB** or **CMYK** with the uploaded ICC filename before downloading. This selection applies to both download buttons independently of the batch export color mode. Without an uploaded ICC file, only sRGB is available. The PDF contains only that Ticket ID.

## Printing caveat

The **Download JPEG + PDF ZIP** package contains a JPEG and a separate single-page PDF for each Ticket ID, named after the Ticket ID (for example, `A-001.jpg` and `A-001.pdf`), with matching filenames in `rgb-jpeg/` and `rgb-pdf/` (or `cmyk-jpeg/` and `cmyk-pdf/`). **Download Master PDF** combines all badges into one PDF.

RGB export requires no ICC upload; JPEG files embed an sRGB profile. Both PDF modes contain one badge per page, including bleed at the template’s physical size.

Both batch download buttons immediately show an export progress panel and stay
disabled until the task finishes. The panel shows badges generated, percentage,
elapsed time, and an approximate remaining time based on the current rendering
speed. The first badge may take longer while fonts load. Finalizing the ZIP or PDF
has its own stage; 100% means the file is ready, not that the browser has saved it.
The browser starts the download when ready, and a link lets you download the same
file again without rendering it again. Large files stream directly to the browser.

To correct a mistake during export, click **Stop export** in the progress panel.
The panel shows **Stopping…** while the current badge or file operation finishes;
then the unfinished ZIP or PDF is deleted and the export controls become available.
You can edit the layout or re-import corrected attendee data and export again.
Earlier completed exports and imported data are preserved. If the file finished
before the stop request arrived, it remains available for download.

Refreshing or leaving during an export requests the browser's standard confirmation
where supported. If you continue, the server keeps working and the page reconnects
to the task after a refresh; use the prepared download link when
ready. A temporary connection failure retries status checks without submitting a
second export. The server also rejects concurrent batch exports from other tabs
and the older download endpoints. Backup and reset stay unavailable during export.
Run one Uvicorn worker and one app instance per data directory for these protections.
Task status and the last 20 task links are held for the current server session;
restarting the server interrupts running tasks. Completed files remain in the
event's `exports` folder until reset.

CMYK export requires an ICC/ICM file. If you are unsure which profile to choose for premium coated paper offset printing, start with [PSO Coated v3 (FOGRA51)](https://registry.color.org/profile-registry/PSOcoated_v3): download `PSOcoated_v3.icc` from that page and select it in the ICC upload field. Use your printer’s specified profile when provided. The app does not automatically download or select a profile. The generated CMYK JPEG package embeds the chosen profile.
PDF/X output intent metadata and printer-specific
imposition are intentionally not claimed as complete in this first implementation; they require
proofing against the actual printer workflow.

## Barcode types

In **Edit layout**, select the barcode layer and choose **Barcode type**: QR Code (default),
Data Matrix (ECC 200), or Aztec. Existing templates remain QR Code unless changed.
All three use the CSV `qr_token` column; its content, including leading/trailing whitespace,
is preserved. ASCII and Unicode content are supported, subject to the selected format's capacity.
Scanner support and character encoding settings determine how scanned Unicode/control characters
are delivered to the receiving application.

Preview and print exports use the same encoder. Barcodes retain their proportions, a quiet zone
of at least four modules, and whole-pixel modules in print output. Enlarge the barcode for longer
content and verify a printed sample with your scanner.

The additional formats use the free, open-source [ZXing-C++](https://github.com/zxing-cpp/zxing-cpp)
Python package (Apache-2.0; bundled Zint components use BSD-3-Clause). Install/update dependencies
with `.venv/bin/pip install -r requirements.txt`; no paid service or separate barcode application is required.

## License

Copyright (c) 2026 BorneoFur.

This project is licensed under the GNU Affero General Public License,
version 3.0. See [LICENSE](LICENSE) for the full license text.