// Browser controller for index.html. Search a control ID to find its event handler.
// API transport lives here; authoritative text/image rendering lives in services.py.
const state = { event: null, templates: [] };
const tierSettings = { id: null };
const $ = (selector) => document.querySelector(selector);

function toast(message, isError = false) {
  const box = $('#toast');
  box.textContent = message;
  box.className = isError ? 'show error' : 'show';
  window.setTimeout(() => { box.className = ''; }, 4200);
}

// Shared API helper: convert FastAPI errors into messages shown by forms and toasts.
async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map((item) => item.msg).join('\n') : body.detail;
    const error = new Error(detail || 'Request failed.');
    error.status = response.status;
    throw error;
  }
  return response;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
}

function renderTemplates() {
  $('#template-list').innerHTML = state.templates.map((template) => {
    const tier = template.name || 'Unnamed Tier';
    const avatarLabel = typeof template.profile_picture === 'boolean'
      ? (template.profile_picture ? 'Avatar enabled' : 'No avatar')
      : 'Avatar setting unavailable';
    const backgroundLabel = template.background_asset
      ? `Background: ${template.background_asset}`
      : 'Background: not uploaded';
    return `
    <article class="template" data-template-id="${escapeHtml(template.id)}">
      <strong>${escapeHtml(tier)}</strong>
      <span>${avatarLabel} · ${escapeHtml(template.id)}</span>
      <small class="background-status">${escapeHtml(backgroundLabel)}</small>
      <label>Background image<input class="background-file" type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png"></label>
      <button class="replace-background" type="button">Upload background</button>
      <button class="edit-tier secondary" type="button">Edit Tier</button>
      <button class="edit-layout secondary" type="button">Edit layout</button>
      ${template.custom ? '<button class="delete-tier danger" type="button">Delete Tier</button>' : ''}
    </article>
  `;
  }).join('') || '<p class="hint">No templates are installed.</p>';
}

async function loadTemplates() {
  state.templates = await (await request('/api/templates')).json();
  renderTemplates();
  await refreshEventSummary().catch((error) => toast(`Could not refresh avatar warnings: ${error.message}`, true));
}

function renderEventSummary() {
  setExportBusy(batchExport.busy);
  const missing = state.event.missing_avatars || [];
  const missingIds = new Set(missing.map((badge) => badge.ticket_id));
  $('#imported-count').textContent = missing.length
    ? `${state.event.record_count} badges imported. ${missing.length} missing avatar${missing.length === 1 ? '' : 's'} to review before printing.`
    : `${state.event.record_count} badges are ready to export.`;
  $('#missing-avatars').classList.toggle('hidden', missing.length === 0);
  $('#missing-avatar-count').textContent = missing.length
    ? `${missing.length} badge${missing.length === 1 ? ' needs an avatar' : 's need avatars'}.`
    : '';
  $('#missing-avatar-list').innerHTML = missing.map((badge) => `<tr><td>${escapeHtml(badge.ticket_id)}</td><td>${escapeHtml(badge.display_name)}</td><td>${escapeHtml(badge.tier)}</td></tr>`).join('');
  const selected = $('#preview-ticket').value;
  $('#preview-ticket').innerHTML = state.event.badges.map((badge) => `<option value="${escapeHtml(badge.ticket_id)}">${escapeHtml(badge.ticket_id)} · ${escapeHtml(badge.display_name)} · ${escapeHtml(badge.tier)}${missingIds.has(badge.ticket_id) ? ' · Missing avatar' : ''}</option>`).join('');
  if (state.event.badges.some((badge) => badge.ticket_id === selected)) $('#preview-ticket').value = selected;
}

let eventSummaryRequest = 0;
async function refreshEventSummary() {
  if (!state.event) return;
  const eventId = state.event.id;
  const version = ++eventSummaryRequest;
  const response = await request(`/api/events/${eventId}/summary`, {cache: 'no-store'});
  const summary = await response.json();
  if (state.event?.id !== eventId || version !== eventSummaryRequest) return;
  state.event = summary;
  renderEventSummary();
}

$('#download-missing-avatars').addEventListener('click', () => {
  const missing = state.event?.missing_avatars || [];
  if (!missing.length) return;
  const cell = (value) => {
    let text = String(value);
    // Keep attendee-controlled values from becoming spreadsheet formulas.
    if (/^[\t\r\n]|^\s*[=+\-@]/.test(text)) text = `'${text}`;
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const rows = [['ticket_id', 'display_name', 'tier'], ...missing.map((badge) => [badge.ticket_id, badge.display_name, badge.tier])];
  const csv = '\uFEFF' + rows.map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n';
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = 'missing-avatars.csv';
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function clearImportError() {
  $('#csv-file').removeAttribute('aria-invalid');
  $('#import-file-error').textContent = '';
  $('#import-file-error').classList.add('hidden');
}

function showImportError(error, filename) {
  const fileError = error.status === 400 || error.status === 422;
  const title = fileError ? 'File error' : 'Import failed';
  const message = fileError
    ? `${filename}: ${error.message}\nCorrect the file, select it again, and retry.`
    : `${error.message}\nPlease retry the import. If it continues to fail, check that the app is running.`;
  if (fileError) $('#csv-file').setAttribute('aria-invalid', 'true');
  $('#import-file-error').textContent = `${title}: ${message}`;
  $('#import-file-error').classList.remove('hidden');
  $('#import-error-title').textContent = title;
  $('#import-error-message').textContent = message;
  $('#import-error-dialog').showModal();
}

$('#csv-file').addEventListener('change', clearImportError);
$('#close-import-error').addEventListener('click', () => $('#import-error-dialog').close());
$('#import-error-dialog').addEventListener('close', () => {
  $('#csv-file').focus();
});

// Import event data: send the spreadsheet and avatars, then populate preview/export controls.
$('#import-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (batchExport.busy) return toast('Please wait for the current export to finish.', true);
  const csv = $('#csv-file').files[0];
  if (!csv) return;
  clearImportError();
  const controls = [...$('#import-form').querySelectorAll('input, button')];
  controls.forEach((control) => { control.disabled = true; });
  const data = new FormData();
  data.append('csv_file', csv, csv.name);
  for (const file of $('#avatar-files').files) {
    data.append('avatar_files', file, file.webkitRelativePath || file.name);
  }
  try {
    const response = await request('/api/import', { method: 'POST', body: data });
    state.event = await response.json();
    batchExport.job = null;
    rememberExport(null);
    $('#export-progress').classList.add('hidden');
    renderEventSummary();
    syncExportColorMode();
    $('#export-section').classList.remove('hidden');
    $('#preview-section').classList.remove('hidden');
    toast(state.event.missing_avatars.length
      ? 'Event imported. Review the missing avatar list before printing.'
      : 'Event imported. Ready to export in RGB; no ICC profile needed.');
  } catch (error) {
    showImportError(error, csv.name);
  } finally {
    controls.forEach((control) => { control.disabled = false; });
  }
});

$('#template-list').addEventListener('click', async (event) => {
  const settingsButton = event.target.closest('.edit-tier');
  if (settingsButton) {
    const card = settingsButton.closest('.template');
    const template = state.templates.find((item) => item.id === card.dataset.templateId);
    tierSettings.id = template.id;
    $('#tier-settings-name').value = template.name;
    $('#tier-settings-avatar').checked = Boolean(template.profile_picture);
    $('#tier-settings').showModal();
    return;
  }
  const deleteButton = event.target.closest('.delete-tier');
  if (deleteButton) {
    const card = deleteButton.closest('.template');
    const template = state.templates.find((item) => item.id === card.dataset.templateId);
    if (!window.confirm(`Delete the custom Tier "${template.name}"? This cannot be undone.`)) return;
    try {
      await request(`/api/templates/${encodeURIComponent(template.id)}`, { method: 'DELETE' });
      await loadTemplates();
      toast('Custom Tier deleted.');
    } catch (error) { toast(error.message, true); }
    return;
  }
  const editButton = event.target.closest('.edit-layout');
  if (editButton) return openEditor(editButton.closest('.template').dataset.templateId);
  const button = event.target.closest('.replace-background');
  if (!button) return;
  const card = button.closest('.template');
  const artwork = card.querySelector('.background-file').files[0];
  if (!artwork) return toast('Choose a background image for this Tier first.', true);
  const data = new FormData(); data.append('artwork', artwork, artwork.name);
  try {
    await request(`/api/templates/${encodeURIComponent(card.dataset.templateId)}/background`, { method: 'POST', body: data });
    await loadTemplates();
    toast('Tier background updated.');
  } catch (error) { toast(error.message, true); }
});

$('#load-format-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const format = $('#saved-format').files[0];
  if (!format) return toast('Choose a formats JSON file first.', true);
  const data = new FormData(); data.append('format_file', format, format.name);
  try { const response = await request('/api/template-formats/import', { method: 'POST', body: data }); const imported = await response.json(); $('#load-format-form').reset(); await loadTemplates(); toast(`${imported.imported} Tier format${imported.imported === 1 ? '' : 's'} loaded.`); } catch (error) { toast(error.message, true); }
});

$('#download-format-json').addEventListener('click', async () => {
  try {
    const response = await request('/api/template-formats/export');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'conbadge-template-formats.json'; anchor.click();
    URL.revokeObjectURL(url); toast('All Tier formats downloaded as JSON.');
  } catch (error) { toast(error.message, true); }
});

// EDIT LAYOUT STATE: manifest is the editable draft; selected is an element index.
// history stores undo/redo snapshots, while savedSnapshot tracks the last saved layout.
// preview and sampleUrl are display-only inputs and do not change attendee records.
const editor = { id: null, manifest: null, selected: 0, history: [], historyIndex: -1, savedSnapshot: null, saving: false, fontLoading: false, maskLoading: false, sampleUrl: '/example.png', previewAttendees: [], preview: {display_name: 'Preview Name', ticket_id: 'S-001', tier: '', qr_token: 'Preview'} };
const isCustomLayer = (element) => element && (element.type === 'artwork' || (element.type === 'text' && Object.hasOwn(element, 'text')));
const elementLabel = (element) => element.name?.trim() || (element.type === 'masked-image' ? 'Avatar' : element.type === 'qr' ? ({qr: 'QR Code', 'data-matrix': 'Data Matrix', aztec: 'Aztec'}[element.symbology || 'qr']) : element.type === 'artwork' ? 'Artwork' : (Object.hasOwn(element, 'text') ? 'Custom text' : element.field));
// Only persistent layout data belongs in snapshots; choosing a preview attendee is not an edit.
const editorSnapshot = () => JSON.stringify({elements: editor.manifest.elements, fonts: editor.manifest.fonts || []});

function hasUnsavedLayout() {
  return editor.manifest && editorSnapshot() !== editor.savedSnapshot;
}

function syncLayoutActions() {
  const dirty = Boolean(hasUnsavedLayout());
  $('#layout-save-status').textContent = editor.maskLoading ? 'Uploading mask…' : editor.fontLoading ? 'Adding font…' : editor.saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'All changes saved';
  $('#layout-save-status').dataset.dirty = String(dirty);
  $('#save-layout').disabled = editor.saving || editor.fontLoading || editor.maskLoading;
  $('#close-editor').disabled = editor.saving || editor.fontLoading || editor.maskLoading;
  $('#undo-layout').disabled = editor.saving || editor.fontLoading || editor.maskLoading || editor.historyIndex <= 0;
  $('#redo-layout').disabled = editor.saving || editor.fontLoading || editor.maskLoading || editor.historyIndex >= editor.history.length - 1;
}

// A new edit after Undo replaces the redo branch. Identical snapshots do not add history.
function rememberEditor() {
  const snapshot = editorSnapshot();
  if (editor.history[editor.historyIndex] === snapshot) return;
  editor.history = editor.history.slice(0, editor.historyIndex + 1); editor.history.push(snapshot); editor.historyIndex = editor.history.length - 1;
  syncLayoutActions();
}

function restoreEditor(index) {
  if (index < 0 || index >= editor.history.length) return;
  const snapshot = JSON.parse(editor.history[index]); editor.manifest.elements = snapshot.elements; editor.manifest.fonts = snapshot.fonts;
  editor.historyIndex = index; editor.selected = Math.min(editor.selected, editor.manifest.elements.length - 1); renderEditor();
}

function setSelectValue(selector, value) { const input = $(selector); input.value = value || ''; if (input.value !== (value || '')) input.value = ''; }

// Fit the physical badge aspect ratio to the visible stage; keep layout values in millimetres.
function fitEditorStage(print) {
  const stage = $('#editor-stage'); const grid = stage.parentElement; const sidebar = grid.querySelector('aside');
  const gap = Number.parseFloat(getComputedStyle(grid).gap) || 18;
  const availableWidth = Math.max(240, grid.clientWidth - sidebar.offsetWidth - gap);
  const ratio = print.width_mm / print.height_mm;
  const availableHeight = Math.max(320, window.innerHeight * .72);
  const width = Math.min(availableWidth, availableHeight * ratio);
  stage.style.width = `${width}px`; stage.style.height = `${width / ratio}px`; stage.style.aspectRatio = `${print.width_mm} / ${print.height_mm}`;
}

function sampleOptions(candidates) {
  editor.previewAttendees = candidates;
  $('#editor-sample').innerHTML = `<option value="/example.png">Template example</option>${candidates.map((item) => `<option value="${escapeHtml(item.avatar_url)}">${escapeHtml(item.ticket_id)} · ${escapeHtml(item.display_name)}</option>`).join('')}`;
  editor.sampleUrl = '/example.png';
}

// Edit layout entry point: load the current manifest, reset history, and initialize samples.
async function openEditor(templateId) {
  try {
    const suffix = state.event ? `?event_id=${encodeURIComponent(state.event.id)}` : '';
    const response = await request(`/api/templates/${encodeURIComponent(templateId)}/editor${suffix}`);
    const data = await response.json();
    clearTextPreviews();
    editor.id = templateId; editor.manifest = data.manifest; editor.selected = 0; editor.history = []; editor.historyIndex = -1; editor.savedSnapshot = editorSnapshot(); rememberEditor();
    $('#editor-title').textContent = `${data.manifest.name} layout`; sampleOptions(data.preview_attendees || []);
    editor.preview = {display_name: 'Preview Name', ticket_id: 'S-001', tier: data.manifest.name, qr_token: 'Preview'};
    $('#preview-display-name').value = editor.preview.display_name; $('#preview-ticket-id').value = editor.preview.ticket_id; $('#preview-tier').value = editor.preview.tier; $('#preview-qr-token').value = editor.preview.qr_token;
    const background = data.manifest.background || {};
    $('#editor-stage').style.backgroundColor = background.color || '#e7edf5';
    $('#editor-background').src = `/api/templates/${encodeURIComponent(templateId)}/background-preview?cache=${Date.now()}`;
    $('#editor-background').onerror = () => { $('#editor-background').removeAttribute('src'); };
    $('#layout-editor').showModal();
    loadGoogleFonts();
    $('#editor-background').onload = renderEditor;
    window.setTimeout(renderEditor, 50);
  } catch (error) { toast(error.message, true); }
}

// TEXT PREVIEW CORE: per-layer raster requests, keyed by draft text settings.
// The server uses the same _text_layer() function as badge previews and exports.
const textPreviews = new Map();

function clearTextPreviews() {
  for (const entry of textPreviews.values()) {
    window.clearTimeout(entry.timer);
    entry.controller.abort();
    if (entry.url) URL.revokeObjectURL(entry.url);
  }
  textPreviews.clear();
}

function renderTextPreview(element, index) {
  const image = document.createElement('img');
  image.className = 'editor-text-sample';
  image.alt = '';
  image.draggable = false;
  // Moving and rotating use the existing raster; only text settings need a new render.
  const payload = {
    element: {...element, x_mm: 0, y_mm: 0, rotation_degrees: 0},
    fonts: editor.manifest.fonts || [],
    value: Object.hasOwn(element, 'text') ? element.text : editor.preview[element.field] ?? '',
  };
  const templateId = editor.id;
  const key = JSON.stringify({templateId, ...payload});
  let entry = textPreviews.get(index);
  if (entry?.key === key) {
    entry.image = image;
    if (entry.url) image.src = entry.url;
    return image;
  }
  if (entry) {
    window.clearTimeout(entry.timer);
    entry.controller.abort();
    if (entry.url) URL.revokeObjectURL(entry.url);
  }
  entry = {key, image, controller: new AbortController(), url: null, timer: null};
  textPreviews.set(index, entry);
  entry.timer = window.setTimeout(async () => {
    try {
      const response = await request(`/api/templates/${encodeURIComponent(templateId)}/text-preview`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: entry.controller.signal,
      });
      const blob = await response.blob();
      // A newer edit may finish first. Never let an older response replace its preview.
      if (textPreviews.get(index) !== entry || entry.controller.signal.aborted) return;
      entry.url = URL.createObjectURL(blob);
      entry.image.src = entry.url;
    } catch (error) {
      if (entry.controller.signal.aborted || textPreviews.get(index) !== entry) return;
      toast(`Text preview: ${error.message}`, true);
      textPreviews.delete(index);
    }
  // Wait briefly for typing/resizing to settle instead of rendering every pointer event.
  }, 120);
  return image;
}

$('#layout-editor').addEventListener('close', clearTextPreviews);

let googleFontsLoaded = false;

// FONT PICKER: Google downloads and uploads become selectable template assets with one shared renderer.
async function loadGoogleFonts(refresh = false) {
  if (googleFontsLoaded && !refresh) return;
  const select = $('#google-font-select');
  const previous = select.value;
  select.disabled = true;
  $('#use-google-font').disabled = true;
  $('#refresh-google-fonts').disabled = true;
  $('#google-font-status').textContent = 'Loading Google Fonts choices…';
  try {
    const fonts = await (await request(`/api/google-fonts${refresh ? '?refresh=true' : ''}`)).json();
    select.innerHTML = '<option value="">Choose a Google font</option>' + fonts.map((font) => `<option value="${escapeHtml(font.id)}">${escapeHtml(font.name)}</option>`).join('');
    if (fonts.some((font) => font.id === previous)) select.value = previous;
    select.disabled = !fonts.length;
    googleFontsLoaded = true;
    $('#google-font-status').textContent = fonts.length ? 'Automatic Noto fallback supports Chinese, Japanese, Korean and Latin. First use downloads fonts; later use works from the local cache.' : 'No Google font choices available. You can upload a TTF or OTF file below.';
  } catch (error) {
    $('#google-font-status').textContent = `Could not load Google Fonts: ${error.message} You can refresh or upload a font file below.`;
  } finally {
    $('#refresh-google-fonts').disabled = false;
    if ($('#layout-editor').open) renderEditor();
  }
}

$('#google-font-select').addEventListener('change', () => {
  const select = $('#google-font-select');
  const name = select.options[select.selectedIndex]?.textContent;
  $('#google-font-link').href = select.value ? `https://fonts.google.com/specimen/${encodeURIComponent(name).replaceAll('%20', '+')}` : 'https://fonts.google.com/';
  renderEditor();
});
$('#google-font-url').addEventListener('input', () => renderEditor(false));
$('#refresh-google-fonts').addEventListener('click', () => loadGoogleFonts(true));
$('#use-google-font').addEventListener('click', async () => {
  const element = editor.manifest?.elements[editor.selected];
  const fontId = $('#google-font-url').value.trim() || $('#google-font-select').value;
  const style = $('#google-font-style').value;
  if (!fontId || element?.type !== 'text' || editor.saving || editor.fontLoading || editor.maskLoading) return;
  editor.fontLoading = true;
  $('#layout-editor .editor-grid').inert = true;
  syncLayoutActions();
  try {
    const result = await (await request(`/api/templates/${encodeURIComponent(editor.id)}/google-fonts`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({font_id: fontId, style}),
    })).json();
    editor.id = result.id;
    editor.manifest.fonts = [...(editor.manifest.fonts || []).filter((font) => font.id !== result.font.id), result.font];
    element.font_family = result.font.id;
    element.font_style = style;
    clearTextPreviews();
    rememberEditor();
    toast('Google font downloaded and applied. Save layout to keep this selection.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    editor.fontLoading = false;
    $('#layout-editor .editor-grid').inert = false;
    renderEditor();
  }
});

// EDITOR DRAWING CORE: rebuild layer boxes and previews from the in-memory manifest.
// updateProperties=false refreshes the stage while preserving an active input and its cursor.
function renderEditor(updateProperties = true) {
  syncLayoutActions();
  if (!editor.manifest) return;
  editor.selected = Math.max(0, Math.min(editor.selected, editor.manifest.elements.length - 1));
  const print = editor.manifest.print; const selected = editor.manifest.elements[editor.selected];
  $('#use-google-font').disabled = editor.fontLoading || selected?.type !== 'text' || !($('#google-font-select').value || $('#google-font-url').value.trim());
  $('#editor-properties').classList.toggle('hidden', !selected);
  $('#layer-backward').disabled = !selected || editor.selected <= 0;
  $('#layer-forward').disabled = !selected || editor.selected >= editor.manifest.elements.length - 1;
  fitEditorStage(print);
  $('#editor-elements').innerHTML = editor.manifest.elements.map((element, index) => `<div class="editor-layer-row"><button class="editor-element ${index === editor.selected ? 'selected' : ''}" data-index="${index}" type="button">${escapeHtml(elementLabel(element))}${element.visible === false ? ' · hidden' : ''}</button>${isCustomLayer(element) ? `<button class="delete-layer danger" data-index="${index}" type="button" aria-label="Delete ${escapeHtml(elementLabel(element))}">Delete</button>` : ''}</div>`).join('') || '<p class="hint">No layers. Add artwork or custom text below.</p>';
  // All visible content shares one stack in manifest order, just like render_badge().
  // Selection handles use a separate overlay so selecting a rear layer cannot raise its image.
  const layers = $('#editor-layers'); layers.innerHTML = '';
  const overlay = $('#editor-overlay'); overlay.innerHTML = '';
  editor.manifest.elements.forEach((element, index) => {
    if (element.visible === false) return;
    const box = document.createElement('div'); box.className = `editor-box ${element.type} ${index === editor.selected ? 'selected' : ''}`; box.dataset.index = index;
    box.style.left = `${element.x_mm / print.width_mm * 100}%`; box.style.top = `${element.y_mm / print.height_mm * 100}%`; box.style.width = `${element.width_mm / print.width_mm * 100}%`; box.style.height = `${element.height_mm / print.height_mm * 100}%`; box.style.transform = `rotate(${element.rotation_degrees || 0}deg)`;
    const layer = document.createElement('div');
    layer.className = 'editor-layer-content';
    for (const property of ['left', 'top', 'width', 'height', 'transform']) layer.style[property] = box.style[property];
    layer.style.zIndex = String(index);
    layers.appendChild(layer);
    if (element.type === 'artwork') {
      const image = document.createElement('img');
      image.className = 'editor-artwork-image';
      image.src = `/api/templates/${encodeURIComponent(editor.id)}/assets/${element.asset}`;
      layer.appendChild(image);
    }
    box.innerHTML = `<span class="box-label">${escapeHtml(elementLabel(element))}</span><span class="box-handle resize" data-handle="resize"></span><span class="box-handle rotate" data-handle="rotate"></span>`;
    if (element.type === 'masked-image') {
      const frame = document.createElement('div');
      frame.className = 'editor-avatar-frame';
      const image = document.createElement('img');
      image.className = 'editor-avatar-sample'; image.src = editor.sampleUrl;
      image.style.objectFit = element.fit || 'cover';
      image.style.transform = `translate(${element.offset_x_pct || 0}%, ${element.offset_y_pct || 0}%) scale(${element.scale || 1})`;
      if (element.mask_asset) {
        // The backend converts grayscale and transparent masks to the same alpha format.
        // Apply it to the fixed frame so moving/scaling the photo does not move the mask.
        const asset = element.mask_asset.split('/').map(encodeURIComponent).join('/');
        const mask = `url("/api/templates/${encodeURIComponent(editor.id)}/mask-preview/${asset}")`;
        frame.style.maskImage = mask; frame.style.webkitMaskImage = mask;
        frame.style.maskSize = '100% 100%'; frame.style.webkitMaskSize = '100% 100%';
        frame.style.maskRepeat = 'no-repeat'; frame.style.webkitMaskRepeat = 'no-repeat';
        frame.style.maskMode = 'alpha';
      }
      frame.appendChild(image); layer.appendChild(frame);
    }
    if (element.type === 'text') layer.appendChild(renderTextPreview(element, index));
    if (element.type === 'qr') { const image = document.createElement('img'); image.className = 'editor-qr-sample'; const query = new URLSearchParams({value: editor.preview.qr_token || 'Preview', foreground: element.foreground_color || '#000000', background: element.background_color || '#ffffff', quiet_zone_modules: String(element.quiet_zone_modules || 4), symbology: element.symbology || 'qr', error_correction: element.error_correction || 'M'}); image.onerror = () => toast("Cannot generate barcode preview. Check the content length and settings.", true); image.src = `/api/barcode-preview?${query}`; layer.appendChild(image); }
    box.addEventListener('pointerdown', beginTransform); overlay.appendChild(box);
  });
  if (!updateProperties || !selected) return;
  $('#layer-name-property').classList.toggle('hidden', !isCustomLayer(selected));
  $('#custom-text-property').classList.toggle('hidden', selected.type !== 'text' || !Object.hasOwn(selected, 'text'));
  $('#prop-layer-name').value = selected.name || '';
  $('#prop-text-content').value = selected.text ?? '';
  $('#artwork-properties').classList.toggle('hidden', selected.type !== 'artwork');
  $('#prop-lock-aspect').checked = selected.lock_aspect_ratio !== false;
  $('#prop-snap-edges').checked = selected.snap_to_edges !== false;
  $('#remove-mask').disabled = !selected.mask_asset || editor.maskLoading;
  $('#mask-status').textContent = selected.mask_asset ? 'Mask applied' : 'No mask';
  const fontOptions = `<option value="">Automatic — multilingual Noto</option>${(editor.manifest.fonts || []).map((font) => `<option value="${escapeHtml(font.id)}">${escapeHtml(font.name)}</option>`).join('')}`;
  $('#prop-font-family').innerHTML = fontOptions;
  $('#prop-x').value = selected.x_mm; $('#prop-y').value = selected.y_mm; $('#prop-w').value = selected.width_mm; $('#prop-h').value = selected.height_mm; $('#prop-rotation').value = selected.rotation_degrees || 0; $('#prop-visible').checked = selected.visible !== false;
  $('#prop-color').value = selected.color || '#000000'; $('#prop-font').value = selected.font_size_pt || ''; $('#prop-min-font').value = selected.min_font_size_pt || ''; $('#prop-max-lines').value = selected.max_lines || 2; setSelectValue('#prop-font-family', selected.font_family); setSelectValue('#prop-font-style', selected.font_style || 'regular'); setSelectValue('#prop-font-language', selected.font_language || 'auto'); setSelectValue('#prop-align', selected.align || 'left'); setSelectValue('#prop-vertical-align', selected.vertical_align || 'middle'); $('#prop-letter-spacing').value = selected.letter_spacing_pt || 0; $('#prop-line-spacing').value = selected.line_spacing_pt || 0;
  setSelectValue('#prop-fit', selected.fit || 'cover'); $('#prop-scale').value = selected.scale || 1; $('#prop-avatar-x').value = selected.offset_x_pct || 0; $('#prop-avatar-y').value = selected.offset_y_pct || 0;
  setSelectValue('#prop-symbology', selected.symbology || 'qr');
  $('#prop-qr-foreground').value = selected.foreground_color || '#000000'; $('#prop-qr-background').value = selected.background_color || '#ffffff'; $('#prop-quiet-zone').value = selected.quiet_zone_modules || 4;
  $('#text-properties').classList.toggle('hidden', selected.type !== 'text'); $('#avatar-properties').classList.toggle('hidden', selected.type !== 'masked-image'); $('#qr-properties').classList.toggle('hidden', selected.type !== 'qr');
  syncLayoutActions();
}

function snapPosition(value, size, total, enabled = $('#snap-layout').checked) {
  if (!enabled) return value;
  const candidates = [0, total - size, (total - size) / 2];
  const closest = candidates.reduce((best, candidate) => Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best);
  return Math.abs(closest - value) < 1.5 ? closest : value;
}

// Preserve the source ratio for new artwork; older layouts use their current proportions.
function artworkRatio(element) {
  return element.aspect_ratio || element.width_mm / element.height_mm;
}

function initialArtworkSize(print, ratio) {
  const width = Math.min(print.width_mm * .5, print.height_mm * .5 * ratio);
  return {width_mm: width, height_mm: width / ratio};
}

// Snap resized artwork against the canvas using its rotated outer bounds.
// The opposite corner stays fixed; a locked ratio gives one scale value to solve for.
function snapArtworkSize(width, height, start, print, ratio) {
  const angle = start.rotation * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
  const anchorX = start.ex + (start.w - c * start.w + s * start.h) / 2;
  const anchorY = start.ey + (start.h - s * start.w - c * start.h) / 2;
  const edges = [
    [Math.min(0, c), Math.min(0, -s), -anchorX],
    [Math.max(0, c), Math.max(0, -s), print.width_mm - anchorX],
    [Math.min(0, s), Math.min(0, c), -anchorY],
    [Math.max(0, s), Math.max(0, c), print.height_mm - anchorY],
  ].filter(([a, b, target]) => a * a + b * b > 1e-10 && Math.abs(a * width + b * height - target) < 1.5);
  const candidates = [];
  for (const [a, b, target] of edges) {
    if (ratio) {
      const scale = a * ratio + b;
      if (Math.abs(scale) > 1e-10) candidates.push([target / scale * ratio, target / scale]);
    } else {
      const correction = (target - a * width - b * height) / (a * a + b * b);
      candidates.push([width + a * correction, height + b * correction]);
    }
  }
  // With the ratio unlocked, two nearby edges can be aligned at once (a corner).
  if (!ratio) {
    edges.forEach(([a, b, target], index) => {
      for (const [d, e, otherTarget] of edges.slice(index + 1)) {
        const determinant = a * e - b * d;
        if (Math.abs(determinant) > 1e-10) {
          candidates.push([(target * e - b * otherTarget) / determinant, (a * otherTarget - target * d) / determinant]);
        }
      }
    });
  }
  const options = candidates.filter(([w, h]) => Number.isFinite(w) && Number.isFinite(h) && w >= .01 && h >= .01)
    .map(([w, h]) => ({w, h, distance: Math.hypot(w - width, h - height),
      matches: edges.filter(([a, b, target]) => Math.abs(a * w + b * h - target) < 1e-6).length}))
    .filter((candidate) => ratio || candidate.distance <= 3)
    .sort((a, b) => b.matches - a.matches || a.distance - b.distance);
  return options.length ? [options[0].w, options[0].h] : [width, height];
}

function resizeArtwork(element, start, dx, dy, print) {
  const angle = start.rotation * Math.PI / 180, cos = Math.cos(angle), sin = Math.sin(angle);
  let width = start.w + dx * cos + dy * sin;
  let height = start.h - dx * sin + dy * cos;
  if (element.lock_aspect_ratio !== false) {
    const ratio = artworkRatio(element);
    element.aspect_ratio = ratio;
    // Project the pointer onto the proportional resize direction, using both axes.
    height = Math.max(.01, .01 / ratio, (ratio * width + height) / (ratio * ratio + 1));
    width = height * ratio;
  } else {
    width = Math.max(.01, width); height = Math.max(.01, height);
  }
  if ($('#snap-layout').checked && element.snap_to_edges !== false) {
    [width, height] = snapArtworkSize(width, height, start, print,
      element.lock_aspect_ratio !== false ? artworkRatio(element) : null);
  }
  element.width_mm = +width.toFixed(4); element.height_mm = +height.toFixed(4);
  // Keep the opposite corner in place, even when the artwork is rotated.
  const dw = element.width_mm - start.w, dh = element.height_mm - start.h;
  element.x_mm = +(start.ex + (cos * dw - sin * dh - dw) / 2).toFixed(4);
  element.y_mm = +(start.ey + (sin * dw + cos * dh - dh) / 2).toFixed(4);
}

function moveArtwork(element, x, y, print) {
  const enabled = $('#snap-layout').checked && element.snap_to_edges !== false;
  const angle = (element.rotation_degrees || 0) * Math.PI / 180;
  const width = Math.abs(Math.cos(angle)) * element.width_mm + Math.abs(Math.sin(angle)) * element.height_mm;
  const height = Math.abs(Math.sin(angle)) * element.width_mm + Math.abs(Math.cos(angle)) * element.height_mm;
  const offsetX = (width - element.width_mm) / 2, offsetY = (height - element.height_mm) / 2;
  // Snap the visible rotated bounds to each axis independently, including all four corners.
  // Do not clamp coordinates: disabling snapping allows images to extend beyond the canvas.
  element.x_mm = +(snapPosition(x - offsetX, width, print.width_mm, enabled) + offsetX).toFixed(4);
  element.y_mm = +(snapPosition(y - offsetY, height, print.height_mm, enabled) + offsetY).toFixed(4);
}

// Drag/resize/rotate: convert screen movement to millimetres, update the draft,
// and add one undo snapshot when the pointer gesture ends.
function beginTransform(event) {
  event.preventDefault(); const action = event.target.dataset.handle || 'move'; editor.selected = Number(event.currentTarget.dataset.index); renderEditor();
  const stage = $('#editor-stage'), element = editor.manifest.elements[editor.selected], print = editor.manifest.print, start = {x: event.clientX, y: event.clientY, ex: element.x_mm, ey: element.y_mm, w: element.width_mm, h: element.height_mm, rotation: element.rotation_degrees || 0};
  const move = (moveEvent) => {
    const dx = (moveEvent.clientX - start.x) / stage.clientWidth * print.width_mm, dy = (moveEvent.clientY - start.y) / stage.clientHeight * print.height_mm;
    if (action === 'resize' && element.type === 'artwork') resizeArtwork(element, start, dx, dy, print);
    else if (action === 'resize') { element.width_mm = Math.max(1, +(start.w + dx).toFixed(2)); element.height_mm = Math.max(1, +(start.h + dy).toFixed(2)); if (element.type === 'qr' || element.type === 'masked-image') element.height_mm = +((start.h / start.w) * element.width_mm).toFixed(2); }
    else if (action === 'rotate') { const rect = stage.getBoundingClientRect(); const cx = rect.left + (start.ex + start.w / 2) / print.width_mm * rect.width, cy = rect.top + (start.ey + start.h / 2) / print.height_mm * rect.height; const startAngle = Math.atan2(start.y - cy, start.x - cx), nextAngle = Math.atan2(moveEvent.clientY - cy, moveEvent.clientX - cx); element.rotation_degrees = +(start.rotation + (nextAngle - startAngle) * 180 / Math.PI).toFixed(1); }
    else if (element.type === 'artwork') moveArtwork(element, start.ex + dx, start.ey + dy, print);
    else { element.x_mm = +snapPosition(Math.max(0, +(start.ex + dx).toFixed(2)), element.width_mm, print.width_mm).toFixed(2); element.y_mm = +snapPosition(Math.max(0, +(start.ey + dy).toFixed(2)), element.height_mm, print.height_mm).toFixed(2); }
    renderEditor();
  };
  const end = () => { window.removeEventListener('pointermove', move); rememberEditor(); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', end, {once: true});
}

// Delete only custom layers. Keep uploaded assets so Undo can restore the layer.
function deleteCustomLayer(index) {
  if (editor.saving || editor.fontLoading || editor.maskLoading) return;
  const element = editor.manifest?.elements[index];
  if (!isCustomLayer(element)) return;
  if (!window.confirm(`Delete layer "${elementLabel(element)}"? You can restore it with Undo before closing the editor.`)) return;
  editor.manifest.elements.splice(index, 1);
  if (index < editor.selected) editor.selected -= 1;
  editor.selected = Math.max(0, Math.min(editor.selected, editor.manifest.elements.length - 1));
  clearTextPreviews();
  rememberEditor();
  renderEditor();
}

$('#editor-elements').addEventListener('click', (event) => {
  const remove = event.target.closest('.delete-layer');
  if (remove) return deleteCustomLayer(Number(remove.dataset.index));
  const button = event.target.closest('.editor-element');
  if (button) { editor.selected = Number(button.dataset.index); renderEditor(); }
});

// Literal text is printed on every badge of this Tier; its name only labels the layer.
$('#add-text-layer').addEventListener('click', () => {
  if (!editor.manifest || editor.saving || editor.fontLoading || editor.maskLoading) return;
  const name = $('#new-text-name').value.trim();
  const text = $('#new-text-content').value;
  if (!name || !text.trim()) return toast('Enter a layer name and text content first.', true);
  const print = editor.manifest.print;
  editor.manifest.elements.push({type: 'text', name, text, x_mm: print.width_mm * .1,
    y_mm: print.height_mm * .4, width_mm: print.width_mm * .8, height_mm: print.height_mm * .15,
    font_size_pt: 24, min_font_size_pt: 9, max_lines: 4, align: 'center', vertical_align: 'middle',
    color: '#ffffff', rotation_degrees: 0, visible: true});
  editor.selected = editor.manifest.elements.length - 1;
  rememberEditor();
  $('#new-text-name').value = ''; $('#new-text-content').value = '';
  renderEditor();
});
$('#editor-properties').addEventListener('input', (event) => {
  if (!editor.manifest || editor.saving) return;
  const key = event.target.dataset.property;
  if (!key) return;
  const element = editor.manifest.elements[editor.selected];
  const ratio = element.type === 'artwork' ? artworkRatio(element) : null;
  element[key] = event.target.type === 'checkbox' ? event.target.checked : event.target.type === 'number' ? Number(event.target.value) : event.target.value;
  if (key === 'font_family') delete element.font_asset;
  // Width/Height inputs obey the same lock as dragging. Keep the original ratio when unlocked.
  if (element.type === 'artwork' && element.lock_aspect_ratio !== false && ['width_mm', 'height_mm', 'lock_aspect_ratio'].includes(key)) {
    element.aspect_ratio = ratio;
    if (key === 'height_mm' && element.height_mm > 0) {
      element.width_mm = +(element.height_mm * ratio).toFixed(4);
      $('#prop-w').value = element.width_mm;
    } else if (element.width_mm > 0) {
      element.height_mm = +(element.width_mm / ratio).toFixed(4);
      $('#prop-h').value = element.height_mm;
    }
  }
  rememberEditor();
  renderEditor(false);
});
$('#editor-properties').addEventListener('change', (event) => { if (event.target.dataset.property) renderEditor(); });
// Preview avatar -> Sample / attendee: change avatar, display name, and Ticket ID together.
// These sample values affect only the editor preview, not saved event data or layout history.
$('#editor-sample').addEventListener('change', (event) => {
  editor.sampleUrl = event.target.value;
  const attendee = editor.previewAttendees.find((item) => item.avatar_url === editor.sampleUrl);
  editor.preview.display_name = attendee ? attendee.display_name : 'Preview Name';
  editor.preview.ticket_id = attendee ? attendee.ticket_id : 'S-001';
  $('#preview-display-name').value = editor.preview.display_name;
  $('#preview-ticket-id').value = editor.preview.ticket_id;
  renderEditor();
});
[['#preview-display-name', 'display_name'], ['#preview-ticket-id', 'ticket_id'], ['#preview-tier', 'tier'], ['#preview-qr-token', 'qr_token']].forEach(([selector, field]) => $(selector).addEventListener('input', (event) => { editor.preview[field] = event.target.value; renderEditor(); }));
$('#undo-layout').addEventListener('click', () => restoreEditor(editor.historyIndex - 1)); $('#redo-layout').addEventListener('click', () => restoreEditor(editor.historyIndex + 1));
$('#layer-backward').addEventListener('click', () => { if (editor.selected <= 0) return; const items = editor.manifest.elements; [items[editor.selected - 1], items[editor.selected]] = [items[editor.selected], items[editor.selected - 1]]; editor.selected -= 1; rememberEditor(); renderEditor(); });
$('#layer-forward').addEventListener('click', () => { const items = editor.manifest.elements; if (editor.selected >= items.length - 1) return; [items[editor.selected + 1], items[editor.selected]] = [items[editor.selected], items[editor.selected + 1]]; editor.selected += 1; rememberEditor(); renderEditor(); });

async function uploadEditorAsset(kind, file) { const data = new FormData(); data.append('kind', kind); data.append('asset', file, file.name); const response = await request(`/api/templates/${encodeURIComponent(editor.id)}/assets`, {method: 'POST', body: data}); return response.json(); }
$('#add-artwork').addEventListener('click', async () => {
  const file = $('#artwork-file').files[0];
  if (!file) return toast('Choose an artwork image first.', true);
  const name = ($('#artwork-layer-name').value.trim() || file.name).slice(0, 120);
  try {
    const result = await uploadEditorAsset('artwork', file);
    editor.id = result.id;
    const print = editor.manifest.print, ratio = result.width_px / result.height_px;
    const size = initialArtworkSize(print, ratio);
    editor.manifest.elements.push({type: 'artwork', name, asset: result.asset,
      x_mm: (print.width_mm - size.width_mm) / 2, y_mm: (print.height_mm - size.height_mm) / 2,
      ...size, aspect_ratio: ratio, lock_aspect_ratio: true, snap_to_edges: true,
      rotation_degrees: 0, visible: true});
    editor.selected = editor.manifest.elements.length - 1;
    rememberEditor();
    $('#artwork-file').value = ''; $('#artwork-layer-name').value = '';
    renderEditor();
  } catch (error) { toast(error.message, true); }
});
// Upload stores an asset immediately; assignment/removal stays in the undoable layout draft.
$('#upload-mask').addEventListener('click', async () => {
  const file = $('#mask-file').files[0], element = editor.manifest?.elements[editor.selected];
  if (!file || element?.type !== 'masked-image') return toast('Select Avatar and choose a PNG mask first.', true);
  if (editor.saving || editor.fontLoading || editor.maskLoading) return;
  editor.maskLoading = true;
  $('#layout-editor .editor-grid').inert = true;
  syncLayoutActions();
  try {
    const result = await uploadEditorAsset('mask', file);
    editor.id = result.id; element.mask_asset = result.asset;
    rememberEditor(); $('#mask-file').value = '';
    toast('Mask applied to the preview. Save layout to keep it.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    editor.maskLoading = false;
    $('#layout-editor .editor-grid').inert = false;
    renderEditor();
  }
});
$('#remove-mask').addEventListener('click', () => {
  const element = editor.manifest?.elements[editor.selected];
  if (element?.type !== 'masked-image' || !element.mask_asset || editor.saving || editor.fontLoading || editor.maskLoading) return;
  delete element.mask_asset;
  rememberEditor(); renderEditor();
  toast('Mask removed. Save layout to keep this change.');
});
// Uploads keep their declared family/style; assignments remain undoable until Save layout.
$('#upload-font').addEventListener('click', async () => {
  const file = $('#font-file').files[0], family = $('#font-family-name').value.trim(), style = $('#font-upload-style').value;
  if (editor.saving || editor.fontLoading || editor.maskLoading) return;
  if (!file || !family) return toast('Enter a font family name and choose a TTF or OTF file.', true);
  const element = editor.manifest?.elements[editor.selected];
  const data = new FormData();
  data.append('family_name', family); data.append('style', style); data.append('font_file', file, file.name);
  editor.fontLoading = true;
  $('#layout-editor .editor-grid').inert = true;
  syncLayoutActions();
  try {
    const result = await (await request(`/api/templates/${encodeURIComponent(editor.id)}/fonts`, {method: 'POST', body: data})).json();
    editor.id = result.id;
    editor.manifest.fonts = [...(editor.manifest.fonts || []).filter((font) => font.id !== result.font.id), result.font];
    if (element?.type === 'text') { element.font_family = result.font.id; element.font_style = style; }
    rememberEditor();
    clearTextPreviews(); $('#font-file').value = '';
    toast('Font uploaded. Save layout to keep your selection.');
  } catch (error) { toast(error.message, true); }
  finally { editor.fontLoading = false; $('#layout-editor .editor-grid').inert = false; renderEditor(); }
});
// SAVE CORE: submit a snapshot and mark it saved only after the API succeeds.
// Lock editing during the request; failures leave the draft available for another attempt.
async function saveLayout() {
  if (editor.saving || editor.fontLoading || editor.maskLoading) return false;
  const snapshot = editorSnapshot();
  editor.saving = true;
  $('#layout-editor .editor-grid').inert = true;
  syncLayoutActions();
  try {
    const response = await request(`/api/templates/${encodeURIComponent(editor.id)}/layout`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: snapshot,
    });
    const result = await response.json();
    editor.id = result.id;
    editor.savedSnapshot = snapshot;
    toast('Layout saved for this Tier.');
    await loadTemplates().catch((error) => toast(`Layout saved, but the Tier list could not refresh: ${error.message}`, true));
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  } finally {
    editor.saving = false;
    $('#layout-editor .editor-grid').inert = false;
    syncLayoutActions();
  }
}

// Both X and Escape use this guard so unsaved work gets the same three-way confirmation.
function requestEditorClose() {
  if (editor.saving || editor.fontLoading || editor.maskLoading) return;
  if (hasUnsavedLayout()) {
    if (!$('#unsaved-layout-dialog').open) $('#unsaved-layout-dialog').showModal();
  } else {
    $('#layout-editor').close();
  }
}

$('#save-layout').addEventListener('click', saveLayout);
$('#close-editor').addEventListener('click', requestEditorClose);
$('#layout-editor').addEventListener('cancel', (event) => { event.preventDefault(); requestEditorClose(); });
$('#keep-editing-layout').addEventListener('click', () => $('#unsaved-layout-dialog').close());
$('#discard-layout').addEventListener('click', () => {
  $('#unsaved-layout-dialog').close();
  $('#layout-editor').close();
});
$('#save-close-layout').addEventListener('click', async () => {
  $('#unsaved-layout-dialog').close();
  if (await saveLayout() && !hasUnsavedLayout()) $('#layout-editor').close();
});

// Editor shortcuts apply only while the editor is open. Text inputs retain native undo/redo.
document.addEventListener('keydown', (event) => {
  if (!$('#layout-editor').open || $('#unsaved-layout-dialog').open || $('#mask-help').open || !(event.ctrlKey || event.metaKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === 's') {
    event.preventDefault();
    if (!editor.saving && !editor.fontLoading && !editor.maskLoading) saveLayout();
    return;
  }
  const editingField = event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"]');
  if (editingField || editor.saving || editor.fontLoading || editor.maskLoading) return;
  if (key === 'z' || (key === 'y' && event.ctrlKey)) {
    event.preventDefault();
    restoreEditor(editor.historyIndex + (key === 'y' || event.shiftKey ? 1 : -1));
  }
});
window.addEventListener('resize', () => { if (editor.manifest && $('#layout-editor').open) renderEditor(); });

$('#tier-settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const name = $('#tier-settings-name').value.trim();
  if (!name) return toast('Tier name cannot be empty.', true);
  try {
    await request(`/api/templates/${encodeURIComponent(tierSettings.id)}/settings`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, profile_picture: $('#tier-settings-avatar').checked}),
    });
    $('#tier-settings').close();
    await loadTemplates();
    toast('Tier settings saved. Update the CSV Tier value if you renamed this Tier.');
  } catch (error) { toast(error.message, true); }
});

$('#close-tier-settings').addEventListener('click', () => $('#tier-settings').close());

$('#add-tier-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const tier = $('#new-tier-name').value.trim();
  const artwork = $('#new-tier-artwork').files[0];
  if (!tier || !artwork) return toast('Enter a Tier name and choose its background image.', true);
  const data = new FormData();
  data.append('tier', tier);
  data.append('artwork', artwork, artwork.name);
  data.append('avatar_enabled', $('#new-tier-avatar').checked ? 'true' : 'false');
  try {
    await request('/api/templates/create', { method: 'POST', body: data });
    $('#add-tier-form').reset();
    await loadTemplates();
    toast(`${tier} was added as a new Tier.`);
  } catch (error) { toast(error.message, true); }
});

let uploadedICCProfile = null;

function markICCUploaded(profile) {
  if (profile && profile === $('#icc-profile').files[0]) {
    uploadedICCProfile = profile;
    syncExportColorMode();
  }
}

// Batch exports run independently of the tab. Poll real work, never a fake timer.
const batchExport = {busy: false, job: null, poll: null, clock: null, updatedAt: 0,
  reconnecting: false, autoDownload: false, profile: null, title: document.title,
  version: 0, stopPending: false, stopError: ''};
const exportStorageKey = 'conbadge-export-job';

function rememberExport(identifier) {
  try {
    if (identifier) sessionStorage.setItem(exportStorageKey, identifier);
    else sessionStorage.removeItem(exportStorageKey);
  } catch (_) { /* Progress still works when browser storage is unavailable. */ }
}

function warnExportLeave(event) {
  event.preventDefault();
  event.returnValue = true;
}

function setExportBusy(busy) {
  batchExport.busy = busy;
  document.querySelectorAll('#export-form button, #export-form select, #export-form input').forEach((control) => {
    control.disabled = busy || !state.event;
  });
  $('#export-form').setAttribute('aria-busy', String(busy));
  $('#zip-export').textContent = busy ? 'Export in progress…' : 'Download JPEG + PDF ZIP';
  $('#pdf-export').textContent = busy ? 'Export in progress…' : 'Download Master PDF';
  const stop = $('#stop-export');
  if (busy && batchExport.job?.status === 'running') stop.classList.remove('hidden');
  else stop.classList.add('hidden');
  stop.disabled = batchExport.stopPending || Boolean(batchExport.job?.cancel_requested);
  stop.textContent = stop.disabled ? 'Stopping…' : 'Stop export';
  if (busy) window.addEventListener('beforeunload', warnExportLeave);
  else window.removeEventListener('beforeunload', warnExportLeave);
}

function exportDuration(seconds) {
  seconds = Math.max(0, Math.ceil(seconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function renderExportTime() {
  const job = batchExport.job;
  if (!job) return;
  const sinceUpdate = batchExport.busy ? (Date.now() - batchExport.updatedAt) / 1000 : 0;
  let estimate = '';
  if (job.status === 'running') {
    if (batchExport.stopPending || job.cancel_requested) estimate = ' · Waiting for the current operation to stop';
    else if (batchExport.reconnecting) estimate = ' · Waiting to reconnect for an updated estimate';
    else if (job.stage === 'packing') estimate = ' · Finalizing file; remaining time varies';
    else if (job.remaining_seconds === null) estimate = ' · Estimating remaining time…';
    else {
      const remaining = job.remaining_seconds - sinceUpdate;
      estimate = remaining > 0 ? ` · About ${exportDuration(remaining)} remaining` : ' · Updating estimate…';
    }
  }
  $('#export-progress-time').textContent = `Elapsed ${exportDuration(job.elapsed_seconds + sinceUpdate)}${estimate}`;
}

function showExportPanel() {
  $('#export-section').classList.remove('hidden');
  $('#export-progress').classList.remove('hidden');
  $('#export-progress-error').classList.add('hidden');
  $('#export-ready-download').classList.add('hidden');
}

function finishExportError(message) {
  batchExport.version++;
  clearTimeout(batchExport.poll);
  clearInterval(batchExport.clock);
  setExportBusy(false);
  document.title = batchExport.title;
  $('#export-progress-title').textContent = 'Export could not finish';
  $('#export-progress-error').textContent = message;
  $('#export-progress-error').classList.remove('hidden');
  $('#export-progress-note').textContent = 'Resolve the issue, then use a download button above to try again.';
  toast(message, true);
}

function renderExportJob(job) {
  batchExport.job = job;
  batchExport.updatedAt = Date.now();
  batchExport.reconnecting = false;
  rememberExport(job.id);
  showExportPanel();
  $('#export-progress-bar').value = job.percent;
  $('#export-progress-percent').textContent = `${job.percent}%`;
  $('#export-progress-count').textContent = `${job.completed} / ${job.total} badges generated · ${job.mode.toUpperCase()} ${job.kind === 'pdf' ? 'Master PDF' : 'JPEG + PDF ZIP'}`;
  $('#export-progress-title').textContent = job.stage === 'packing'
    ? (job.kind === 'pdf' ? 'Saving Master PDF…' : 'Finalizing ZIP…')
    : job.stage === 'preparing' ? 'Preparing export…' : 'Generating badges…';
  $('#export-progress-note').textContent = 'Your export is running. Please keep this page open; there is no need to click again.';
  if (batchExport.stopPending || job.cancel_requested) {
    $('#export-progress-title').textContent = 'Stopping export…';
    $('#export-progress-note').textContent = 'Finishing the current badge or file operation, then removing the unfinished export. Please wait before making corrections.';
  } else if (batchExport.stopError && job.status === 'running') {
    $('#export-progress-error').textContent = batchExport.stopError;
    $('#export-progress-error').classList.remove('hidden');
  }
  setExportBusy(job.status === 'running');
  document.title = job.status === 'running' ? `${job.percent}% · Exporting badges` : batchExport.title;
  renderExportTime();
  if (job.status === 'running') return;
  clearTimeout(batchExport.poll);
  clearInterval(batchExport.clock);
  if (job.status === 'failed') {
    finishExportError(job.error);
    return;
  }
  if (job.status === 'cancelled') {
    batchExport.autoDownload = false;
    $('#export-progress-title').textContent = 'Export stopped';
    $('#export-progress-note').textContent = 'The unfinished ZIP or PDF was removed. You can now correct your data or layout and export again.';
    return;
  }
  $('#export-progress-title').textContent = 'Export ready — 100%';
  const link = $('#export-ready-download');
  link.href = job.download_url;
  link.download = job.filename;
  link.textContent = `Download ${job.filename}`;
  link.classList.remove('hidden');
  $('#export-progress-note').textContent = 'Your file is ready. Use the link below to download it again without regenerating badges.';
  if (job.mode === 'cmyk') markICCUploaded(batchExport.profile);
  if (batchExport.autoDownload) {
    batchExport.autoDownload = false;
    // Native download streams to disk without buffering a large ZIP in JS.
    link.click();
    $('#export-progress-note').textContent = 'Download started. Check your browser downloads. If it did not start, use the link below.';
    toast('Export ready. Download started.');
  }
}

async function pollExportJob(identifier) {
  const version = batchExport.version;
  try {
    const response = await request(`/api/export-jobs/${identifier}`, {cache: 'no-store'});
    const job = await response.json();
    if (version !== batchExport.version) return;
    renderExportJob(job);
    if (!batchExport.busy) return;
  } catch (error) {
    if (version !== batchExport.version) return;
    if (error.status === 404) {
      rememberExport(null);
      finishExportError(error.message);
      return;
    }
    batchExport.reconnecting = true;
    $('#export-progress-title').textContent = 'Reconnecting to export…';
    $('#export-progress-note').textContent = 'The connection was interrupted. Your export may still be running. Reconnecting automatically; no need to export again.';
    renderExportTime();
  }
  batchExport.poll = window.setTimeout(() => pollExportJob(identifier), batchExport.reconnecting ? 2500 : 1000);
}

function watchExportJob(job) {
  batchExport.version++;
  clearTimeout(batchExport.poll);
  clearInterval(batchExport.clock);
  renderExportJob(job);
  if (batchExport.busy) {
    batchExport.clock = window.setInterval(renderExportTime, 1000);
    batchExport.poll = window.setTimeout(() => pollExportJob(job.id), 1000);
  }
}

async function stopExport() {
  const job = batchExport.job;
  if (!batchExport.busy || !job || batchExport.stopPending || job.cancel_requested) return;
  batchExport.stopPending = true;
  batchExport.stopError = '';
  batchExport.autoDownload = false;
  batchExport.version++;
  clearTimeout(batchExport.poll);
  // Pause polling while sending the stop so an older running response cannot
  // replace the server's cancellation result or re-enable the stop button.
  renderExportJob(job);
  try {
    const response = await request(`/api/export-jobs/${job.id}/cancel`, {method: 'POST'});
    const result = await response.json();
    batchExport.stopPending = false;
    watchExportJob(result);
    if (result.status === 'completed') {
      $('#export-progress-note').textContent = 'This export finished before it could be stopped. You can correct your data or layout and export again.';
    }
  } catch (error) {
    batchExport.stopPending = false;
    if (error.status === 404) {
      rememberExport(null);
      finishExportError(error.message);
      return;
    }
    batchExport.stopError = 'Could not confirm the stop request. The export may still be running. Try Stop export again.';
    watchExportJob(job);
    toast(batchExport.stopError, true);
  }
}

async function recoverExport() {
  // Check the server even with no saved tab state: another tab may be exporting.
  const response = await request('/api/export-jobs/active', {cache: 'no-store'});
  let job = (await response.json()).job;
  let identifier;
  try { identifier = sessionStorage.getItem(exportStorageKey); } catch (_) { /* Optional storage. */ }
  if (!job && identifier) {
    try { job = await (await request(`/api/export-jobs/${identifier}`, {cache: 'no-store'})).json(); }
    catch (error) {
      if (error.status !== 404) throw error;
      rememberExport(null);
      showExportPanel();
      finishExportError(error.message);
    }
  }
  if (!job) { setExportBusy(false); return false; }
  if (!state.event) {
    try {
      state.event = await (await request(`/api/events/${job.event_id}/summary`, {cache: 'no-store'})).json();
      renderEventSummary();
      $('#preview-section').classList.remove('hidden');
    } catch (_) { /* Export status and the prepared download remain usable. */ }
  }
  watchExportJob(job);
  return true;
}

// Export for print: lock synchronously, before the first network await.
async function exportBadges(kind) {
  if (!state.event || batchExport.busy) return;
  const profile = $('#icc-profile').files[0];
  const mode = $('#export-color-mode').value;
  if (mode === 'cmyk' && !profile) return toast('Choose a printer ICC / ICM profile first.', true);
  batchExport.version++;
  batchExport.stopPending = false;
  batchExport.stopError = '';
  setExportBusy(true);
  showExportPanel();
  batchExport.job = null;
  batchExport.autoDownload = true;
  batchExport.profile = profile;
  $('#export-progress-title').textContent = 'Starting export…';
  $('#export-progress-bar').removeAttribute('value');
  $('#export-progress-percent').textContent = '';
  $('#export-progress-count').textContent = `Preparing ${state.event.record_count} badges`;
  $('#export-progress-time').textContent = 'Estimating remaining time…';
  $('#export-progress-note').textContent = 'Your request is being sent. Please keep this page open; there is no need to click again.';
  const data = new FormData();
  // Know the ID before POST so a lost response can be recovered without resubmitting.
  const identifier = crypto.randomUUID().replaceAll('-', '');
  data.append('request_id', identifier);
  if (mode === 'cmyk') data.append('icc_profile', profile, profile.name);
  rememberExport(identifier);
  try {
    const response = await request(`/api/events/${state.event.id}/export-jobs/${mode}/${kind}`, {method: 'POST', body: data});
    watchExportJob(await response.json());
  } catch (error) {
    if (error.status && error.status !== 409 && error.status < 500) {
      rememberExport(null);
      finishExportError(error.message);
      return;
    }
    if (error.status === 409) {
      batchExport.autoDownload = false;
      batchExport.profile = null;
      toast('Another operation is running. Checking for an existing export…');
    }
    try {
      if (!await recoverExport()) finishExportError(error.message);
    } catch (_) {
      // Keep the controls locked while the outcome is unknown; never resubmit.
      pollExportJob(identifier);
    }
  }
}

function syncExportColorMode() {
  const cmyk = $('#export-color-mode').value === 'cmyk';
  $('#icc-profile-label').classList.toggle('hidden', !cmyk);
  $('#icc-profile-help').classList.toggle('hidden', !cmyk);
  $('#zip-icc-note').classList.toggle('hidden', !cmyk);
  $('#pdf-icc-note').classList.toggle('hidden', !cmyk);
  $('#icc-profile').required = cmyk;
  const profile = $('#icc-profile').files[0];
  const uploaded = profile && profile === uploadedICCProfile;
  $('#icc-profile-badge').classList.toggle('hidden', !profile);
  $('#icc-profile-badge').textContent = profile ? (uploaded ? '✅ Uploaded' : '✅ Selected') : '';
  $('#icc-profile-status').classList.toggle('hidden', !cmyk);
  $('#icc-profile-status').dataset.state = uploaded ? 'uploaded' : (profile ? 'selected' : 'empty');
  $('#icc-status-title').textContent = uploaded
    ? '✅ ICC profile uploaded and applied'
    : (profile ? '✅ ICC profile selected — next: download' : '1. Choose your printer ICC / ICM profile');
  $('#icc-status-filename').textContent = profile ? profile.name : 'No profile selected';
  $('#icc-status-action').textContent = profile
    ? 'Click Download JPEG + PDF ZIP or Download Master PDF below.'
    : 'Select an .icc or .icm file above, then choose a download format below.';
  $('#icc-status-transfer').textContent = uploaded
    ? 'This profile was used successfully. Each new CMYK download sends this same file to the app again and uses it to convert your badges.'
    : (profile
      ? 'This file has not been uploaded yet. Downloading sends the ICC file shown above to the app and uses it to convert your badges to CMYK. No separate upload step is needed.'
      : 'The chosen profile will be sent to the app and applied when you download. For sRGB without an ICC file, choose RGB above.');
}

$('#icc-profile').addEventListener('change', () => {
  uploadedICCProfile = null;
  syncExportColorMode();
});
$('#export-color-mode').addEventListener('change', syncExportColorMode);
window.addEventListener('pageshow', syncExportColorMode);
syncExportColorMode();

$('#export-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  await exportBadges('jpeg');
});

$('#pdf-export').addEventListener('click', async () => exportBadges('pdf'));
$('#stop-export').addEventListener('click', stopExport);

let previewDownload = null;

function syncPreviewColorMode() {
  const useICC = $('#preview-color-mode').value === 'icc';
  const profile = previewDownload?.profile;
  $('#preview-download-mode').textContent = useICC && profile
    ? `JPG and PDF will use CMYK with ${profile.name}. The profile is uploaded and applied when you click Download.`
    : profile
      ? 'JPG and PDF will use sRGB. The selected ICC profile will not be applied.'
      : 'JPG and PDF will use sRGB. To use CMYK, select an ICC / ICM file under Export for print and reopen this preview.';
  $('#preview-download-error').classList.add('hidden');
}

$('#preview-color-mode').addEventListener('change', syncPreviewColorMode);

// View a badge download: export the selected Ticket ID using the preview dialog color mode.
async function downloadPreview(kind) {
  if (!previewDownload) return;
  const { eventId, ticketId } = previewDownload;
  const profile = $('#preview-color-mode').value === 'icc' ? previewDownload.profile : null;
  const buttons = [$('#preview-download-jpg'), $('#preview-download-pdf'), $('#preview-color-mode')];
  buttons.forEach((button) => { button.disabled = true; });
  $('#preview-download-error').classList.add('hidden');
  const data = new FormData();
  data.append('ticket_id', ticketId);
  if (profile) data.append('icc_profile', profile, profile.name);
  try {
    const response = await request(`/api/events/${eventId}/badge-export/${kind}`, { method: 'POST', body: data });
    const url = URL.createObjectURL(await response.blob());
    markICCUploaded(profile);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = response.headers.get('content-disposition')?.match(/filename="([^";]+)"/)?.[1] || `badge.${kind}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    $('#preview-download-error').textContent = error.message;
    $('#preview-download-error').classList.remove('hidden');
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

$('#preview-download-jpg').addEventListener('click', () => downloadPreview('jpg'));
$('#preview-download-pdf').addEventListener('click', () => downloadPreview('pdf'));

$('#open-preview').addEventListener('click', () => {
  if (!state.event) return;
  const ticketId = $('#preview-ticket').value;
  $('#preview-image').src = `/api/events/${state.event.id}/records/${encodeURIComponent(ticketId)}/preview?cache=${Date.now()}`;
  $('#preview-title').textContent = ticketId;
  const profile = $('#icc-profile').files[0] || null;
  previewDownload = { eventId: state.event.id, ticketId, profile };
  $('#preview-icc-option').disabled = !profile;
  $('#preview-icc-option').textContent = profile ? `CMYK — ${profile.name}` : 'CMYK — no ICC profile selected';
  $('#preview-color-mode').value = profile ? 'icc' : 'srgb';
  syncPreviewColorMode();
  $('#preview-download-error').classList.add('hidden');
  $('#preview-dialog').showModal();
});

$('#close-preview').addEventListener('click', () => $('#preview-dialog').close());

// Mask instructions open above the layout editor; closing returns focus to the help button.
$('#open-mask-help').addEventListener('click', () => $('#mask-help').showModal());
$('#close-mask-help').addEventListener('click', () => $('#mask-help').close());
$('#mask-help').addEventListener('close', () => $('#open-mask-help').focus());

$('#open-import-help').addEventListener('click', () => $('#import-help').showModal());
$('#close-import-help').addEventListener('click', () => $('#import-help').close());

// Backups use a native browser download, so a large ZIP is never buffered in JS.
const maintenance = {busy: false, token: null, resetRoot: null};

function maintenanceStatus(message, isError = false) {
  document.querySelectorAll('[data-maintenance-status]').forEach((element) => {
    element.textContent = message;
    element.classList.toggle('file-error', isError);
  });
}

function syncMaintenanceActions() {
  for (const selector of ['#backup-data', '#open-reset-data', '#backup-before-reset', '#cancel-reset-data', '#reset-data-confirmation']) {
    $(selector).disabled = maintenance.busy;
  }
  $('#confirm-reset-data').disabled = maintenance.busy || !maintenance.resetRoot || $('#reset-data-confirmation').value !== 'DELETE';
  $('#reset-data-dialog').setAttribute('aria-busy', String(maintenance.busy));
}

async function maintenanceHeaders() {
  if (!maintenance.token) {
    const response = await request('/api/data/session', {cache: 'no-store'});
    maintenance.token = (await response.json()).token;
  }
  return {'Content-Type': 'application/json', 'X-Badge-Data-Token': maintenance.token};
}

async function backupSavedData() {
  if (maintenance.busy) return;
  maintenance.busy = true;
  syncMaintenanceActions();
  maintenanceStatus('Preparing your backup. Keep this page open…');
  try {
    const headers = await maintenanceHeaders();
    const response = await request('/api/data/backup', {method: 'POST', headers, body: '{}'});
    const backup = await response.json();
    const anchor = document.createElement('a');
    anchor.href = backup.download_url;
    anchor.download = backup.filename;
    anchor.referrerPolicy = 'no-referrer';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    maintenanceStatus('Backup download started. Check your browser downloads and wait until the ZIP is saved before resetting.');
  } catch (error) {
    if (error.status === 403) maintenance.token = null;
    maintenanceStatus(`Backup failed: ${error.message} No saved data was deleted.`, true);
  } finally {
    maintenance.busy = false;
    syncMaintenanceActions();
  }
}

$('#backup-data').addEventListener('click', backupSavedData);
$('#backup-before-reset').addEventListener('click', backupSavedData);
$('#open-reset-data').addEventListener('click', async () => {
  maintenance.resetRoot = null;
  maintenance.busy = true;
  $('#reset-data-confirmation').value = '';
  $('#reset-data-path').textContent = 'Checking the data directory…';
  $('#reset-data-entries').textContent = '';
  $('#reset-data-preserved').textContent = '';
  maintenanceStatus('');
  syncMaintenanceActions();
  $('#reset-data-dialog').showModal();
  try {
    const response = await request('/api/data/reset-preview', {cache: 'no-store'});
    const preview = await response.json();
    maintenance.resetRoot = preview.data_root;
    $('#reset-data-path').textContent = preview.data_root;
    $('#reset-data-entries').textContent = preview.delete_entries.join(', ') || 'No saved app data found.';
    $('#reset-data-preserved').textContent = preview.preserved_entries.join(', ') || 'None currently present.';
  } catch (error) {
    $('#reset-data-path').textContent = 'Reset unavailable';
    maintenanceStatus(error.message, true);
  } finally {
    maintenance.busy = false;
    syncMaintenanceActions();
  }
});
$('#reset-data-confirmation').addEventListener('input', syncMaintenanceActions);
$('#cancel-reset-data').addEventListener('click', () => $('#reset-data-dialog').close());
$('#reset-data-dialog').addEventListener('cancel', (event) => {
  if (maintenance.busy) event.preventDefault();
});
$('#reset-data-dialog').addEventListener('close', () => $('#open-reset-data').focus());
$('#reset-data-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (maintenance.busy || !maintenance.resetRoot || $('#reset-data-confirmation').value !== 'DELETE') return;
  maintenance.busy = true;
  syncMaintenanceActions();
  maintenanceStatus('Deleting saved data…');
  try {
    const headers = await maintenanceHeaders();
    await request('/api/data/reset', {method: 'POST', headers,
      body: JSON.stringify({confirmation: 'DELETE', data_root: maintenance.resetRoot})});
    // Drop stale attendee selections, editor drafts, preview URLs, and file inputs.
    window.location.reload();
  } catch (error) {
    if (error.status === 403) maintenance.token = null;
    maintenanceStatus(`Reset failed: ${error.message}`, true);
    maintenance.busy = false;
    syncMaintenanceActions();
  }
});

async function restoreExportOnLoad() {
  setExportBusy(true);
  try {
    const restored = await recoverExport();
    if (!restored && $('#export-progress-error').classList.contains('hidden')) {
      $('#export-progress').classList.add('hidden');
      if (!state.event) $('#export-section').classList.add('hidden');
    }
  } catch (_) {
    showExportPanel();
    $('#export-progress-title').textContent = 'Checking for an existing export…';
    $('#export-progress-note').textContent = 'Reconnecting to the app before starting another export.';
    batchExport.poll = window.setTimeout(restoreExportOnLoad, 2500);
  }
}

restoreExportOnLoad();
loadTemplates().catch((error) => toast(error.message, true));
