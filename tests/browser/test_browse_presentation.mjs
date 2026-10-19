import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/browse_presentation.js', import.meta.url), 'utf8');
function harness(shape = 'auto') {
  const gallery = {dataset: {galleryShape: 'auto', mediaScope: 'album'}};
  const control = {value: shape, addEventListener(name, fn) { this[name] = fn; }};
  const input = {value: 'auto'};
  const hint = {textContent: ''};
  const type = {value: 'article'};
  const radio = {form: {requestSubmit() { this.submitted = true; }}, addEventListener(name, fn) { this[name] = fn; }};
  const link = (href, attrs = []) => ({href, hasAttribute: (name) => attrs.includes(name)});
  const links = [
    link('http://localhost/navigator/?source=local&source=bookmarks&media=album&cursor=next&ui=curator'),
    link('http://localhost/resources/demo/?return_to=%2Fnavigator%2F%3Fmedia%3Dalbum%26tags%3Ddesign%23item-demo'),
    link('https://example.com/'),
    link('http://localhost/api/catalog/items?media=album'),
    link('http://localhost/navigator/?ui=baseline', ['data-ui-fixed']),
    link('http://localhost/resources/demo/?return_to=https%3A%2F%2Fevil.example%2Fnavigator%2F'),
    link('http://localhost/both/folder/?src=internal&offset=24'),
  ];
  const location = {href: `http://localhost/navigator/?media=album&source=local&source=bookmarks&tags=design&tags=art&seed=123&cursor=current&context=saved&ui=curator&shape=${shape}#item-demo`};
  const selectors = {'[data-gallery-shape]': gallery, '[data-gallery-shape-option]': control,
    '[data-shape-input]': input, '[data-gallery-shape-hint]': hint, '#navigator-type': type};
  const window = {location, history: {state: {example: true}, replaceState(state, _, url) { this.state = state; location.href = url.href; }},
    document: {querySelector: (s) => selectors[s], querySelectorAll: (s) => s === 'a[href]' ? links : [radio]}};
  vm.runInNewContext(source, {window, URL});
  return {window, gallery, control, input, hint, type, radio, links};
}

test('thumbnail changes keep the same result nodes, repeated filters and anchored return paths', () => {
  const state = harness();
  const node = state.gallery;
  state.control.value = 'portrait';
  state.control.change();
  assert.equal(state.gallery, node);
  assert.equal(node.dataset.galleryShape, 'portrait');
  assert.equal(state.input.value, 'portrait');
  const url = new URL(state.window.location.href);
  assert.deepEqual(url.searchParams.getAll('source'), ['local', 'bookmarks']);
  assert.deepEqual(url.searchParams.getAll('tags'), ['design', 'art']);
  for (const [key, value] of Object.entries({media: 'album', seed: '123', cursor: 'current', context: 'saved', ui: 'curator'})) assert.equal(url.searchParams.get(key), value);
  assert.equal(url.hash, '#item-demo');
  assert.equal(state.window.history.state.example, true);
  assert.equal(new URL(state.links[0].href, url).searchParams.get('shape'), 'portrait');
  const back = new URL(new URL(state.links[1].href, url).searchParams.get('return_to'), url);
  assert.equal(back.searchParams.get('media'), 'album');
  assert.equal(back.searchParams.get('shape'), 'portrait');
  assert.equal(back.hash, '#item-demo');
  assert.equal(new URL(state.links[6].href, url).searchParams.get('src'), 'internal');
  assert.equal(new URL(state.links[6].href, url).searchParams.get('shape'), 'portrait');
  assert.equal(state.links[2].href, 'https://example.com/');
  assert.equal(state.links[3].href, 'http://localhost/api/catalog/items?media=album');
  assert.equal(state.links[4].href, 'http://localhost/navigator/?ui=baseline');
  assert.equal(state.links[5].href, 'http://localhost/resources/demo/?return_to=https%3A%2F%2Fevil.example%2Fnavigator%2F');
});

test('an explicit shape reloads from URL, automatic shape clears overrides and invalid values recover', () => {
  const state = harness('square');
  assert.equal(state.gallery.dataset.galleryShape, 'square');
  state.control.value = 'auto';
  state.control.change();
  assert.equal(new URL(state.window.location.href).searchParams.has('shape'), false);
  assert.equal(state.gallery.dataset.galleryShape, 'auto');
  assert.match(state.hint.textContent, /3:4/);
  assert.equal(harness('invalid').control.value, 'auto');
});

test('changing content submits the complete search form and clears a conflicting exact type', () => {
  const state = harness('portrait');
  state.radio.change();
  assert.equal(state.type.value, '');
  assert.equal(state.radio.form.submitted, true);
  assert.equal(state.input.value, 'portrait');
});
