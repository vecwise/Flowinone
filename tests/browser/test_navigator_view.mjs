import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_view.js', import.meta.url), 'utf8');

function harness(storedView = null) {
  const gallery = {dataset: {galleryView: 'grid'}};
  const buttons = ['grid', 'list', 'shelf'].map((view) => ({
    dataset: {galleryViewOption: view},
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, handler) { this[name] = handler; },
  }));
  const values = new Map(storedView ? [['flowinone-gallery-view', storedView]] : []);
  const window = {
    document: {
      querySelector: () => gallery,
      querySelectorAll: () => buttons,
    },
    localStorage: {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value),
    },
  };
  vm.runInNewContext(source, {window});
  return {gallery, buttons, values};
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
