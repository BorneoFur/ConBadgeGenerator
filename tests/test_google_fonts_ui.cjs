// Run the shipped download handler to check that the layer uses the returned font style.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

const source = readFileSync('app/static/app.js', 'utf8');
const handler = source.slice(source.indexOf("$('#use-google-font').addEventListener"),
  source.indexOf('// EDITOR DRAWING CORE:'));

function setup(style, result) {
  const elements = new Map();
  const $ = (selector) => {
    if (!elements.has(selector)) elements.set(selector, {value: '',
      addEventListener(name, callback) {this[name] = callback;}});
    return elements.get(selector);
  };
  $('#google-font-url').value = 'https://fonts.google.com/specimen/Molle';
  $('#google-font-style').value = style;
  const layer = {type: 'text', font_family: 'previous', font_style: 'regular'};
  const editor = {id: 'attendees', selected: 0, manifest: {elements: [layer], fonts: []}};
  const calls = [], messages = [], savedStyles = [];
  const context = vm.createContext({$, editor,
    syncLayoutActions() {}, clearTextPreviews() {}, renderEditor() {},
    rememberEditor() {savedStyles.push(layer.font_style);},
    toast(message) {messages.push(message);},
    request: async (url, options) => {
      calls.push({url, payload: JSON.parse(options.body)});
      return {json: async () => result};
    }});
  vm.runInContext(handler, context);
  return {click: () => $('#use-google-font').click(), $, editor, layer, calls, messages, savedStyles};
}

test('Regular on an italic-only family applies the returned Italic asset and updates controls', async () => {
  const font = {id: 'molle', name: 'Molle', italic: 'fonts/molle-italic.ttf'};
  const ui = setup('regular', {id: 'tier-attendees', font, style: 'italic'});
  await ui.click();
  assert.equal(ui.calls[0].payload.style, 'regular');
  assert.equal(ui.editor.id, 'tier-attendees');
  assert.equal(ui.layer.font_family, font.id);
  assert.equal(ui.layer.font_style, 'italic');
  assert.ok(ui.editor.manifest.fonts[0][ui.layer.font_style]);
  assert.equal(ui.$('#google-font-style').value, 'italic');
  assert.deepEqual(ui.savedStyles, ['italic']);
  assert.match(ui.messages[0], /only provides Italic/);
  assert.equal(ui.editor.fontLoading, false);
  assert.equal(ui.$('#layout-editor .editor-grid').inert, false);
});

test('an exact style match keeps the requested style', async () => {
  const ui = setup('bold', {id: 'tier-attendees', font: {id: 'example', bold: 'fonts/bold.ttf'}, style: 'bold'});
  await ui.click();
  assert.equal(ui.layer.font_style, 'bold');
  assert.equal(ui.$('#google-font-style').value, 'bold');
  assert.match(ui.messages[0], /Google font downloaded and applied/);
});

test('responses without style keep the requested style', async () => {
  const ui = setup('italic', {id: 'tier-attendees', font: {id: 'molle', italic: 'fonts/italic.ttf'}});
  await ui.click();
  assert.equal(ui.layer.font_style, 'italic');
  assert.deepEqual(ui.savedStyles, ['italic']);
});
