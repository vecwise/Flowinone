import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_view.js', import.meta.url), 'utf8');

function harness(storedView = null, {href = 'http://localhost/navigator/?scope=all'} = {}) {
  const gallery = {dataset: {galleryView: 'grid'}};
  const buttons = ['grid', 'list'].map((view) => ({
    dataset: {galleryViewOption: view},
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this[name] = handler; },
  }));
  const values = new Map(storedView ? [['flowinone-gallery-view', storedView]] : []);
  const location = {href, origin: 'http://localhost', assign(value) { this.href = value; this.assigned = value; }};
  const viewInput = {value: ''};
  const readingLink = {href: 'http://localhost/resources/resource-1/?return_to=%2Fnavigator%2F%3Fscope%3Dall%26tags%3DML%23item-example'};
  const window = {
    document: {
      querySelector: (selector) => selector === '[data-gallery-view]' ? gallery : viewInput,
      querySelectorAll: (selector) => selector === '[data-gallery-view-option]' ? buttons : [readingLink],
    },
    location,
    history: {replaceState(_state, _title, value) { location.href = new URL(value, location.href).toString(); }},
    localStorage: {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value),
    },
  };
  vm.runInNewContext(source, {window, URL});
  return {gallery, buttons, values, location, viewInput, readingLink};
}

test('switching presentation keeps the same result nodes and saves the preference', () => {
  const {gallery, buttons, values} = harness();
  const sameGallery = gallery;
  buttons[1].click();
  assert.equal(gallery, sameGallery);
  assert.equal(gallery.dataset.galleryView, 'list');
  assert.equal(buttons[1].attrs['aria-pressed'], 'true');
  assert.equal(buttons[0].attrs['aria-pressed'], 'false');
  assert.equal(values.get('flowinone-gallery-view'), 'list');
  assert.equal(harness('list').gallery.dataset.galleryView, 'list');
});

test('an unknown saved mode falls back to the grid', () => {
  assert.equal(harness('unknown').gallery.dataset.galleryView, 'grid');
});

test('legacy modes migrate without loading a different page or losing the query', () => {
  for (const mode of ['knowledge', 'shelf']) {
    const oldUrl = `http://localhost/navigator/?scope=gallery&source=bookmarks&tags=ML&view=${mode}#item-example`;
    const result = harness(null, {href: oldUrl});
    const url = new URL(result.location.href);
    const expected = mode === 'knowledge' ? 'list' : 'grid';
    assert.equal(result.gallery.dataset.galleryView, expected);
    assert.equal(result.location.assigned, undefined);
    assert.equal(url.searchParams.get('tags'), 'ML');
    assert.equal(url.searchParams.get('source'), 'bookmarks');
    assert.equal(url.hash, '#item-example');
    assert.equal(url.searchParams.get('view'), expected === 'list' ? 'list' : null);
  }
  assert.equal(harness('knowledge').gallery.dataset.galleryView, 'list');
  assert.equal(harness('shelf').gallery.dataset.galleryView, 'grid');
});

test('an explicit grid link overrides a saved list preference', () => {
  const result = harness('list', {href: 'http://localhost/navigator/?scope=all&view=grid'});
  assert.equal(result.gallery.dataset.galleryView, 'grid');
  assert.equal(result.viewInput.value, '');
  assert.equal(result.values.get('flowinone-gallery-view'), 'grid');
  assert.equal(harness(result.values.get('flowinone-gallery-view'), {href: result.location.href}).gallery.dataset.galleryView, 'grid');
});

test('reading links return to the same layout and theme', () => {
  const result = harness();
  result.buttons[1].click();
  const returnUrl = new URL(new URL(result.readingLink.href, 'http://localhost').searchParams.get('return_to'), 'http://localhost');
  assert.equal(returnUrl.searchParams.get('tags'), 'ML');
  assert.equal(returnUrl.searchParams.get('view'), 'list');
  assert.equal(returnUrl.hash, '#item-example');
});
