// Controller checks use the shipped code with a small DOM and deterministic network.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');

const source = readFileSync('app/static/app.js', 'utf8');
const controller = source.slice(source.indexOf('// Batch exports run independently'), source.indexOf('function syncExportColorMode()'));

function setup(network) {
  const elements = new Map();
  const $ = (selector) => {
    if (!elements.has(selector)) {
      const classes = new Set(['hidden']);
      elements.set(selector, {textContent: '', disabled: false, value: '', files: [], clicks: 0,
        classList: {add: (name) => classes.add(name), remove: (name) => classes.delete(name), contains: (name) => classes.has(name)},
        setAttribute(name, value) {this[name] = value;}, removeAttribute(name) {delete this[name];},
        click() {this.clicks++;}});
    }
    return elements.get(selector);
  };
  $('#export-color-mode').value = 'rgb';
  const controls = ['#zip-export', '#pdf-export', '#export-color-mode', '#icc-profile'].map($);
  const listeners = new Map();
  const storage = new Map();
  const calls = [];
  const context = vm.createContext({$, crypto: webcrypto, FormData, Date,
    state: {event: {id: 'event-123', record_count: 3}},
    document: {title: 'Badge Generator', querySelectorAll: () => controls},
    window: {addEventListener: (name, fn) => listeners.set(name, fn),
      removeEventListener: (name) => listeners.delete(name), setTimeout: () => 1, setInterval: () => 2},
    clearTimeout() {}, clearInterval() {}, toast() {}, markICCUploaded() {}, renderEventSummary() {},
    sessionStorage: {setItem: (key, value) => storage.set(key, value), getItem: (key) => storage.get(key), removeItem: (key) => storage.delete(key)},
    request: async (url, options = {}) => {
      calls.push({url, options});
      return {json: async () => network(url, options)};
    }});
  vm.runInContext(controller, context);
  return {context, $, calls, listeners, storage, controls, run: (code) => vm.runInContext(code, context)};
}

const job = (overrides = {}) => ({id: 'a'.repeat(32), event_id: 'event-123', mode: 'rgb', kind: 'jpeg',
  status: 'running', stage: 'rendering', completed: 1, total: 3, percent: 25,
  elapsed_seconds: 2, remaining_seconds: 6, error: null, download_url: null, cancel_requested: false, ...overrides});

test('rapid ZIP/PDF clicks submit once and guard navigation immediately', async () => {
  let respond;
  const pending = new Promise((resolve) => {respond = resolve;});
  const ui = setup(() => pending);
  const first = ui.run('exportBadges("jpeg")');
  await ui.run('exportBadges("jpeg")');
  await ui.run('exportBadges("pdf")');
  assert.equal(ui.calls.length, 1);
  assert.ok(ui.controls.every((control) => control.disabled));
  let prevented = false;
  ui.listeners.get('beforeunload')({preventDefault() {prevented = true;}});
  assert.ok(prevented);
  assert.equal(ui.$('#export-progress-title').textContent, 'Starting export…');
  assert.equal(ui.$('#export-progress-bar').value, undefined);
  respond(job());
  await first;
  assert.equal(ui.$('#export-progress-percent').textContent, '25%');
  assert.match(ui.$('#export-progress-time').textContent, /About .* remaining/);
});

test('finalization stays below 100; completion downloads once and unlocks', async () => {
  const ui = setup(() => job());
  await ui.run('exportBadges("jpeg")');
  ui.context.snapshot = job({stage: 'packing', completed: 3, percent: 75, remaining_seconds: null});
  ui.run('renderExportJob(snapshot)');
  assert.equal(ui.$('#export-progress-title').textContent, 'Finalizing ZIP…');
  assert.equal(ui.$('#export-ready-download').clicks, 0);
  ui.context.snapshot = job({status: 'completed', stage: 'completed', completed: 3, percent: 100,
    remaining_seconds: null, filename: 'badges.zip', download_url: '/api/export-jobs/a/download'});
  ui.run('renderExportJob(snapshot); renderExportJob(snapshot)');
  assert.equal(ui.$('#export-progress-percent').textContent, '100%');
  assert.equal(ui.$('#export-ready-download').clicks, 1);
  assert.ok(ui.controls.every((control) => !control.disabled));
  assert.equal(ui.listeners.has('beforeunload'), false);
  assert.equal(ui.context.document.title, 'Badge Generator');
});

test('refresh recovers active task without starting a new render', async () => {
  const ui = setup((url) => url.endsWith('/active') ? {job: job()} : {id: 'event-123', record_count: 3});
  ui.run('state.event = null');
  assert.equal(await ui.run('recoverExport()'), true);
  assert.equal(ui.calls.filter(({options}) => options.method === 'POST').length, 0);
  assert.equal(ui.$('#export-progress-percent').textContent, '25%');
  assert.equal(ui.run('state.event.id'), 'event-123');
  assert.ok(ui.listeners.has('beforeunload'));
});

test('transient polling failure keeps buttons locked and never resubmits', async () => {
  const ui = setup((url) => {
    if (url.includes('/export-jobs/rgb/')) return job();
    throw new TypeError('offline');
  });
  await ui.run('exportBadges("jpeg")');
  await ui.run(`pollExportJob('${'a'.repeat(32)}')`);
  assert.equal(ui.$('#export-progress-title').textContent, 'Reconnecting to export…');
  assert.ok(ui.controls.every((control) => control.disabled));
  assert.equal(ui.calls.filter(({options}) => options.method === 'POST').length, 1);
  assert.ok(ui.listeners.has('beforeunload'));
});

test('a lost start response recovers its ID even when the job already finished', async () => {
  let identifier;
  const ui = setup((url, options) => {
    if (options.method === 'POST') {
      identifier = options.body.get('request_id');
      throw new TypeError('response lost');
    }
    if (url.endsWith('/active')) return {job: null};
    assert.ok(url.endsWith(identifier));
    return job({id: identifier, status: 'completed', stage: 'completed', percent: 100,
      download_url: '/ready.zip', filename: 'ready.zip'});
  });
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.calls.filter(({options}) => options.method === 'POST').length, 1);
  assert.equal(ui.$('#export-ready-download').clicks, 1);
  assert.equal(ui.run('batchExport.busy'), false);
});

test('failed export shows its reason and permits retry', async () => {
  const ui = setup(() => job({status: 'failed', stage: 'failed', error: 'Invalid profile.'}));
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.$('#export-progress-error').textContent, 'Invalid profile.');
  assert.equal(ui.$('#export-ready-download').clicks, 0);
  assert.equal(ui.run('batchExport.busy'), false);
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.calls.length, 2);
});

test('another tab owns the running export; conflict recovery does not auto-download it', async () => {
  const ui = setup((url, options) => {
    if (options.method === 'POST') throw Object.assign(new Error('Busy'), {status:409});
    if (url.endsWith('/active')) return {job:job({kind:'pdf'})};
    throw new Error('Unexpected request');
  });
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.run('batchExport.job.kind'), 'pdf');
  assert.equal(ui.run('batchExport.autoDownload'), false);
  assert.equal(ui.calls.filter(({options}) => options.method === 'POST').length, 1);
  assert.ok(ui.controls.every((control) => control.disabled));
});

test('server restart reports lost task and releases the navigation guard', async () => {
  const ui = setup((url, options) => {
    if (options.method === 'POST') return job();
    throw Object.assign(new Error('Server restarted; please export again.'), {status:404});
  });
  await ui.run('exportBadges("jpeg")');
  await ui.run(`pollExportJob('${'a'.repeat(32)}')`);
  assert.equal(ui.run('batchExport.busy'), false);
  assert.equal(ui.listeners.has('beforeunload'), false);
  assert.equal(ui.storage.size, 0);
  assert.match(ui.$('#export-progress-error').textContent, /Server restarted/);
});

test('stop clicks submit once and keep export locked until cleanup is done', async () => {
  let respond;
  const pending = new Promise((resolve) => {respond = resolve;});
  const ui = setup((url) => url.endsWith('/cancel') ? pending : job());
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.$('#stop-export').classList.contains('hidden'), false);
  const first = ui.run('stopExport()');
  await ui.run('stopExport()');
  assert.equal(ui.calls.filter(({url}) => url.endsWith('/cancel')).length, 1);
  assert.equal(ui.$('#stop-export').disabled, true);
  assert.equal(ui.$('#export-progress-title').textContent, 'Stopping export…');
  assert.ok(ui.controls.every((control) => control.disabled));
  respond(job({cancel_requested:true, stage:'stopping', remaining_seconds:null}));
  await first;
  assert.equal(ui.run('batchExport.busy'), true);
  assert.ok(ui.listeners.has('beforeunload'));
  ui.context.snapshot = job({cancel_requested:true, status:'cancelled', stage:'cancelled', remaining_seconds:null});
  ui.run('renderExportJob(snapshot)');
  assert.equal(ui.$('#export-progress-title').textContent, 'Export stopped');
  assert.equal(ui.$('#stop-export').classList.contains('hidden'), true);
  assert.equal(ui.$('#export-ready-download').classList.contains('hidden'), true);
  assert.equal(ui.$('#export-ready-download').clicks, 0);
  assert.ok(ui.controls.every((control) => !control.disabled));
  assert.equal(ui.listeners.has('beforeunload'), false);
  await ui.run('exportBadges("jpeg")');
  assert.equal(ui.calls.filter(({url}) => url.includes('/export-jobs/rgb/')).length, 2);
});

test('a stale poll cannot replace an accepted stop request', async () => {
  let respond;
  const pending = new Promise((resolve) => {respond = resolve;});
  const ui = setup((url, options) => {
    if (url.endsWith('/cancel')) return job({stage:'stopping', cancel_requested:true});
    return options.method === 'POST' ? job() : pending;
  });
  await ui.run('exportBadges("jpeg")');
  const poll = ui.run(`pollExportJob('${'a'.repeat(32)}')`);
  await ui.run('stopExport()');
  respond(job());
  await poll;
  assert.equal(ui.run('batchExport.job.stage'), 'stopping');
  assert.equal(ui.$('#stop-export').disabled, true);
  assert.equal(ui.$('#export-progress-title').textContent, 'Stopping export…');
});

test('refresh during stopping preserves the guard and does not repeat the stop', async () => {
  const ui = setup(() => ({job:job({stage:'stopping', cancel_requested:true})}));
  assert.equal(await ui.run('recoverExport()'), true);
  assert.equal(ui.$('#export-progress-title').textContent, 'Stopping export…');
  assert.equal(ui.$('#stop-export').disabled, true);
  assert.equal(ui.run('batchExport.busy'), true);
  assert.ok(ui.listeners.has('beforeunload'));
  assert.equal(ui.calls.filter(({options}) => options.method === 'POST').length, 0);
});

test('failed stop request permits another stop attempt while keeping export locked', async () => {
  const ui = setup((url) => {
    if (url.endsWith('/cancel')) throw new TypeError('offline');
    return job();
  });
  await ui.run('exportBadges("jpeg")');
  await ui.run('stopExport()');
  assert.equal(ui.run('batchExport.busy'), true);
  assert.equal(ui.$('#stop-export').disabled, false);
  assert.match(ui.$('#export-progress-error').textContent, /Could not confirm the stop request/);
  assert.equal(ui.run('batchExport.autoDownload'), false);
  await ui.run('stopExport()');
  assert.equal(ui.calls.filter(({url}) => url.endsWith('/cancel')).length, 2);
});

test('completion wins a late stop; offer the file without auto-downloading', async () => {
  const ui = setup((url) => url.endsWith('/cancel')
    ? job({status:'completed', stage:'completed', percent:100, download_url:'/ready.zip', filename:'ready.zip'}) : job());
  await ui.run('exportBadges("jpeg")');
  await ui.run('stopExport()');
  assert.equal(ui.$('#export-ready-download').clicks, 0);
  assert.equal(ui.$('#export-ready-download').classList.contains('hidden'), false);
  assert.match(ui.$('#export-progress-note').textContent, /finished before it could be stopped/);
  assert.equal(ui.run('batchExport.busy'), false);
});
