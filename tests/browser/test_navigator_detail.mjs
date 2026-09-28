import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_detail.js', import.meta.url), 'utf8');

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.children = [];
    this.listeners = {};
    this.hidden = false;
    this.textContent = '';
  }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  removeAttribute(name) { delete this[name]; }
  focus() { this.focused = true; }
  click(event = {}) { return this.listeners.click?.(event); }
}

function harness() {
  const names = [
    'status', 'content', 'image', 'source', 'title', 'description', 'tags',
    'launch', 'related', 'back', 'close',
  ];
  const parts = Object.fromEntries(names.map((name) => [name, new Element()]));
  const dialog = new Element();
  dialog.querySelector = (selector) => parts[selector.replace('[data-gallery-detail-', '').replace(']', '')];
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; dialog.listeners.close?.(); };
  const link = new Element();
  link.closest = () => ({dataset: {navigatorItem: 'first'}});
  const page = new Element({navigatorQueryPayload: JSON.stringify({sources: ['bookmarks']})});
  const document = {
    querySelector: (selector) => selector === '[data-gallery-detail]' ? dialog : page,
    querySelectorAll: () => [link],
    createElement: () => new Element(),
  };
  const requests = [];
  const items = {
    first: {id: 'first', title: 'First', item_type: 'bookmark', launch_source_label: '書籤', launch_uri: 'https://example.test/first', target_blank: true, tags: ['design']},
    second: {id: 'second', title: 'Second', item_type: 'image', launch_source_label: '本機', launch_uri: '/image/second', tags: ['design']},
  };
  const window = {
    document,
    fetch: async (url, options) => {
      requests.push({url, options});
      const id = url.includes('/second') ? 'second' : 'first';
      const payload = options ? {} : url.includes('/related')
        ? {items: id === 'first' ? [items.second] : []}
        : items[id];
      return {ok: true, json: async () => payload};
    },
  };
  vm.runInNewContext(source, {window, URLSearchParams});
  return {parts, dialog, link, requests};
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('gallery detail stays in place, follows related items, and returns focus', async () => {
  const {parts, dialog, link, requests} = harness();
  let prevented = false;
  link.click({button: 0, preventDefault() { prevented = true; }});
  await flush();
  assert.equal(prevented, true);
  assert.equal(dialog.open, true);
  assert.equal(parts.title.textContent, 'First');
  assert.equal(parts.launch.href, 'https://example.test/first');
  assert.equal(parts.related.children.length, 1);
  assert.match(requests[0].url, /source=bookmarks/);

  parts.related.children[0].click();
  await flush();
  assert.equal(parts.title.textContent, 'Second');
  assert.equal(parts.back.hidden, false);
  parts.back.click();
  await flush();
  assert.equal(parts.title.textContent, 'First');
  parts.close.click();
  assert.equal(dialog.open, false);
  assert.equal(link.focused, true);
});
