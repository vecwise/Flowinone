import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_view.js', import.meta.url), 'utf8');

function harness(storedView = null, {href = 'http://localhost/navigator/?scope=all', knowledgeLoaded = false} = {}) {
  const gallery = {dataset: {galleryView: 'grid', knowledgeLoaded: String(knowledgeLoaded)}};
  const buttons = ['grid', 'list', 'shelf', 'knowledge'].map((view) => ({
    dataset: {galleryViewOption: view},
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this[name] = handler; },
  }));
  const values = new Map(storedView ? [['flowinone-gallery-view', storedView]] : []);
  const location = {href, origin: 'http://localhost', assign(value) { this.href = value; this.assigned = value; }};
  const viewInput = {value: ''};
  const window = {
    document: {
      querySelector: (selector) => selector === '[data-gallery-view]' ? gallery : viewInput,
      querySelectorAll: (selector) => selector === '[data-gallery-view-option]' ? buttons : [],
    },
    location,
    history: {replaceState(_state, _title, value) { location.href = new URL(value, location.href).toString(); }},
    localStorage: {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value),
    },
  };
  vm.runInNewContext(source, {window, URL});
  return {gallery, buttons, values, location, viewInput};
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

test('knowledge view loads extracted previews and keeps the Catalog URL', () => {
  const entering = harness();
  entering.buttons[3].click();
  assert.equal(new URL(entering.location.assigned).searchParams.get('view'), 'knowledge');
  const loaded = harness(null, {href: entering.location.assigned, knowledgeLoaded: true});
  assert.equal(loaded.gallery.dataset.galleryView, 'knowledge');
  loaded.buttons[0].click();
  assert.equal(loaded.viewInput.value, '');
  assert.equal(new URL(loaded.location.href).searchParams.has('view'), false);
});
