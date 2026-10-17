import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/ui_switcher.js', import.meta.url), 'utf8');

function element(tagName, attrs = {}, dataset = {}) {
  return {tagName, dataset, children: [], value: '',
    getAttribute: (key) => attrs[key] ?? null,
    setAttribute: (key, value) => { attrs[key] = value; },
    hasAttribute: (key) => key in attrs,
    querySelector() { return this.children.find((child) => child.name === 'ui') || null; },
    querySelectorAll() { return []; },
    appendChild(child) { this.children.push(child); },
  };
}

function setup(href = 'http://localhost/navigator/?source=local&source=eagle&tags=A&tags=B&cursor=opaque&seed=42&context=ctx#item-one') {
  const select = {value: 'baseline', selectedOptions: [{textContent: 'Test UI'}],
    addEventListener(name, handler) { this[name] = handler; }};
  const root = {
    documentElement: {dataset: {ui: 'baseline'}}, body: {},
    share: {value: 'http://localhost/navigator/views/saved/?tags=A&tags=B'},
    links: [element('A', {href: '/resources/one/?return_to=%2Fnavigator%2F%3Ftags%3DA%26tags%3DB%23item-one'}),
      element('A', {href: '/navigator/?ui=reader', 'data-ui-fixed': ''}),
      element('A', {href: '/api/catalog/items?source=local'}),
      element('A', {href: 'https://example.com/'})],
    forms: [element('FORM', {method: 'get'}), element('FORM', {method: 'post', action: '/resources/?ui=curator'}),
      element('FORM', {method: 'post', action: '/api/catalog/saved-searches'}),
      element('FORM', {method: 'dialog'}), element('FORM')],
    querySelectorAll(selector) {
      return ({'a[href]': this.links, form: this.forms, '[data-ui-select]': [select], '[data-context-share-url]': [this.share]})[selector] || [];
    },
    querySelector: (selector) => selector === '[data-ui-select]' ? select : null,
    createElement: () => ({}),
    addEventListener(name, handler) { this[name] = handler; },
  };
  const state = {focusedItem: 'one'};
  const window = {
    document: root, location: {href}, history: {state,
      replaceState(nextState, _title, next) { assert.equal(nextState, state); window.location.href = new URL(next, window.location.href).href; }},
    addEventListener(name, handler) { this[name] = handler; },
  };
  vm.runInNewContext(source, {window, URL});
  return {window, root, select, api: window.FlowinoneUI};
}

test('switching preserves repeated filters, cursor, seed, context and selected-item anchor', () => {
  const {window, root, select} = setup();
  select.value = 'workbench'; select.change();
  const url = new URL(window.location.href);
  assert.equal(root.documentElement.dataset.ui, 'workbench');
  assert.deepEqual(url.searchParams.getAll('source'), ['local', 'eagle']);
  assert.deepEqual(url.searchParams.getAll('tags'), ['A', 'B']);
  assert.equal(url.searchParams.get('cursor'), 'opaque');
  assert.equal(url.searchParams.get('seed'), '42');
  assert.equal(url.searchParams.get('context'), 'ctx');
  assert.equal(url.hash, '#item-one');
  assert.equal(root.forms[0].children[0].value, 'workbench');
  assert.equal(new URL(root.forms[1].getAttribute('action'), window.location.href).searchParams.get('ui'), 'workbench');
  assert.equal(root.forms[2].children.length, 0); // API contracts stay untouched.
  assert.equal(root.forms[3].children.length, 0); // Dialog close semantics stay untouched.
  assert.equal(root.forms[4].children.length, 0); // JS-only forms own their payloads.
  assert.equal(new URL(root.share.value).searchParams.get('ui'), 'workbench');
  assert.deepEqual(new URL(root.share.value).searchParams.getAll('tags'), ['A', 'B']);
  assert.equal(new URL(root.share.value).pathname, '/navigator/views/saved/');
});

test('normal navigation follows the selected UI while fixed A/B links and APIs stay independent', () => {
  const {window, root, api} = setup();
  api.controller.apply('reader');
  const destination = new URL(root.links[0].getAttribute('href'), window.location.href);
  const returnTo = new URL(destination.searchParams.get('return_to'), window.location.href);
  assert.equal(destination.searchParams.get('ui'), 'reader');
  assert.deepEqual(returnTo.searchParams.getAll('tags'), ['A', 'B']);
  assert.equal(returnTo.searchParams.get('ui'), 'reader');
  assert.equal(returnTo.hash, '#item-one');
  api.controller.apply('curator');
  assert.equal(root.links[1].getAttribute('href'), '/navigator/?ui=reader');
  assert.equal(root.links[2].getAttribute('href'), '/api/catalog/items?source=local');
  assert.equal(root.links[3].getAttribute('href'), 'https://example.com/');
  const dynamic = element('A', {href: '/image/a.jpg?src=internal&return_to=%2Fboth%2Fboard%2F%3Fsrc%3Dinternal'});
  root.click({target: {closest: () => dynamic}});
  assert.equal(new URL(dynamic.getAttribute('href'), window.location.href).searchParams.get('ui'), 'curator');
  assert.equal(new URL(dynamic.getAttribute('href'), window.location.href).searchParams.get('src'), 'internal');
});

test('UI URL decoration excludes files, download routes, APIs and off-origin URLs', () => {
  const {api} = setup();
  for (const value of ['/api/catalog/items', '/static/favicon.svg', '/serve_video/a.mp4',
    '/resources/id/asset/cover', '/catalog/video/id', 'https://example.com/resources/id/', '//example.com/', '#related']) {
    assert.equal(api.withUI(value, 'reader', 'http://localhost/navigator/'), value);
  }
  for (const value of ['/navigator/', '/navigator/views/id/', '/resources/id/versions/', '/image/a.jpg',
    '/video/a.mp4', '/EAGLE_folder/id/', '/EAGLE_image/id/', '/catalog/duplicate-review/', '/route-guide/', '/ui-lab/']) {
    assert.equal(new URL(api.withUI(value, 'reader', 'http://localhost/'), 'http://localhost').searchParams.get('ui'), 'reader');
  }
});

test('entering Lab from baseline captures the live query and anchor without requiring a UI preference', () => {
  const {root} = setup();
  const link = element('A', {href: '/ui-lab/', 'data-ui-lab-entry': ''});
  root.click({target: {closest: () => link}});
  const lab = new URL(link.getAttribute('href'), 'http://localhost/');
  const previous = new URL(lab.searchParams.get('return_to'), 'http://localhost/');
  assert.deepEqual(previous.searchParams.getAll('tags'), ['A', 'B']);
  assert.equal(previous.hash, '#item-one');
  assert.equal(lab.searchParams.has('ui'), false);
});
