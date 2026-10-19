import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/browse_thumbnails.js', import.meta.url), 'utf8');
const id = n => n.toString(16).padStart(40, '0');
const item = (n, status, extra = {}) => ({id: id(n), title: `Bookmark ${n}`, status, provider: 'metadata', thumbnail_url: `/images/${n}`, image_version: 'v2', ...extra});

class Element {
  constructor(dataset = {}) { this.dataset = dataset; this.hidden = false; this.open = false; this.disabled = false; this.textContent = ''; this.value = 'page'; this.nodes = new Map(); this.listeners = new Map(); this.children = []; this.attrs = new Map(); this.classList = {remove() {}}; }
  querySelector(s) { return this.nodes.get(s) || null; }
  querySelectorAll(s) { return this.nodes.get(s) || []; }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  setAttribute(k, v) { this.attrs.set(k, v); }
  getAttribute(k) { return this.attrs.get(k) || null; }
  append(node) { this.children.push(node); }
  prepend(node) { this.children.unshift(node); this.nodes.set('[data-thumbnail-image]', node); }
  replaceChildren(fragment) { this.children = [...fragment.children]; }
  remove() {}
  trigger(type) { return this.listeners.get(type)?.(); }
}

function harness({saved = null, statuses = [], post, offline = false} = {}) {
  const tools = new Element({chromePage: 'true'});
  const controls = {};
  for (const key of ['scope', 'batch', 'run', 'progress', 'results', 'result-summary', 'result-list']) {
    controls[key] = new Element();
    tools.nodes.set(`[data-thumbnail-${key}]`, controls[key]);
  }
  const status = new Element();
  const card = new Element({thumbnailId: id(1), thumbnailStatus: 'retry'});
  const media = new Element();
  card.nodes.set('.gallery-card-media', media);
  card.nodes.set('[data-thumbnail-state]', new Element());
  const button = new Element({thumbnailRetry: id(1)});
  const documentListeners = new Map();
  const storage = new Map(saved ? [['flowinone.thumbnail-retry.v1', JSON.stringify(saved)]] : []);
  const timers = new Map();
  const requests = [];
  let timerId = 0;
  let isOffline = offline;
  const document = {
    querySelector: () => tools,
    getElementById: () => status,
    querySelectorAll: s => s === '[data-thumbnail-id]' ? [card] : [button],
    createElement: () => new Element(),
    createDocumentFragment: () => new Element(),
    addEventListener: (type, callback) => documentListeners.set(type, callback),
  };
  const request = async (url, options = {}) => {
    requests.push({url, options});
    if (isOffline) throw new Error('network offline');
    if (options.method === 'POST') return post ? post(url, options) : {ok: true, json: async () => ({queued: [id(1)], items: [item(1, 'queued')]})};
    const ids = new URL(url, 'http://localhost').searchParams.get('ids').split(',');
    return {ok: true, json: async () => ({items: ids.map(mid => statuses.find(r => r.id === mid) || item(1, 'ready'))})};
  };
  const window = {
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout: key => timers.delete(key),
  };
  vm.runInNewContext(source, {document, window, fetch: request, URL, console});
  documentListeners.get('DOMContentLoaded')();
  return {tools, controls, status, card, media, button, requests, storage, timers,
    setOffline: value => { isOffline = value; },
    single: async () => {
      documentListeners.get('click')({target: {closest: () => button}, preventDefault() {}});
      await new Promise(resolve => setImmediate(resolve));
    },
    tick: async () => {
      const [key, {callback}] = timers.entries().next().value;
      timers.delete(key);
      await callback();
    },
  };
}

test('single retry forces refresh, disables duplicate actions, updates fallback and cache version', async () => {
  const h = harness();
  assert.equal(h.tools.open, false);
  await h.single();
  assert.equal(h.tools.open, true);
  assert.equal(h.requests[0].url, '/api/bookmark-thumbnails/enqueue');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {ids: [id(1)], force: true});
  assert.equal(h.button.disabled, true);
  await h.single();
  assert.equal(h.requests.length, 1);
  await h.tick();
  assert.match(h.status.textContent, /本輪完成 1 \/ 1：成功 1、失敗 0/);
  assert.equal(h.button.disabled, false);
  assert.equal(h.media.querySelector('[data-thumbnail-image]').getAttribute('src'), '/images/1?v=v2');
  assert.equal(h.card.dataset.thumbnailStatus, 'ready');
});

test('batch sends the chosen scope and includes only page IDs for page scope', async () => {
  const h = harness();
  await h.controls.batch.trigger('click');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {scope: 'page', ids: [id(1)]});
  await h.tick();
  h.controls.scope.value = 'all';
  await h.controls.batch.trigger('click');
  assert.deepEqual(JSON.parse(h.requests.at(-1).options.body), {scope: 'all'});
});

test('resumes a batch above 200 IDs, counts one attempt as finished on retry and shows reasons safely', async () => {
  const statuses = Array.from({length: 205}, (_, n) => item(n + 1, n < 100 ? 'ready' : 'retry', n < 100 ? {} : {provider: 'site-tile', title: '<img onerror=alert(1)>', error: 'HTTP 403'}));
  const h = harness({saved: {started: Date.now(), items: statuses.map(r => ({...r, status: 'queued'}))}, statuses});
  assert.equal(h.button.disabled, true);
  assert.equal(h.tools.open, true);
  await h.tick();
  assert.equal(h.requests.length, 2);
  assert.match(h.status.textContent, /本輪完成 205 \/ 205：成功 100、失敗 105/);
  assert.equal(h.controls.progress.value, 205);
  assert.equal(h.controls['result-list'].children.length, 205);
  assert.match(h.controls['result-list'].children[0].textContent, /<img onerror=alert\(1\)>：網站拒絕存取/);
  assert.equal(h.button.disabled, false);
});

test('poll failures retain progress and retry automatically; submission errors permit retry', async () => {
  const h = harness({saved: {started: Date.now(), items: [item(1, 'queued')]}, offline: true});
  await h.tick();
  assert.match(h.status.textContent, /暫時無法取得最新進度/);
  assert.equal(h.button.disabled, true);
  assert.equal([...h.timers.values()][0].delay, 9000);
  h.setOffline(false);
  await h.tick();
  assert.match(h.status.textContent, /成功 1/);
  const rejected = harness({post: () => ({ok: false, json: async () => ({error: 'service unavailable'})})});
  await rejected.controls.batch.trigger('click');
  assert.match(rejected.status.textContent, /無法排入重爬工作/);
  assert.equal(rejected.button.disabled, false);
  assert.equal(rejected.controls.batch.disabled, false);
});

test('empty batch has a clear result and no polling loop', async () => {
  const h = harness({post: () => ({ok: true, json: async () => ({queued: [], items: []})})});
  await h.controls.batch.trigger('click');
  assert.match(h.status.textContent, /沒有需要重試/);
  assert.equal(h.controls.progress.hidden, true);
  assert.equal(h.timers.size, 0);
});

test('restored completed runs restore the versioned image and permit another retry', () => {
  const h = harness({saved: {started: Date.now(), items: [item(1, 'ready')]}});
  assert.equal(h.media.querySelector('[data-thumbnail-image]').getAttribute('src'), '/images/1?v=v2');
  assert.equal(h.button.disabled, false);
  assert.equal(h.tools.open, false);
  assert.match(h.status.textContent, /本輪完成 1 \/ 1/);
});
