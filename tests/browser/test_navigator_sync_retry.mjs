import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controllerSource = fs.readFileSync(
  new URL('../../static/js/navigator_sync.js', import.meta.url),
  'utf8',
);

class FakeElement {
  constructor({dataset = {}, hidden = false, textContent = ''} = {}) {
    this.dataset = {...dataset};
    this.hidden = hidden;
    this.disabled = false;
    this.textContent = textContent;
    this.listeners = new Map();
    this.children = new Map();
  }

  addEventListener(event, listener) {
    this.listeners.set(event, listener);
  }

  querySelector(selector) {
    return this.children.get(selector) || null;
  }

  trigger(event) {
    return this.listeners.get(event)?.({currentTarget: this});
  }
}

function makeRow(source, status, detailText) {
  const row = new FakeElement({dataset: {catalogSyncSource: source, status}});
  row.children.set('[data-catalog-sync-state]', new FakeElement({textContent: status}));
  row.children.set('[data-catalog-sync-detail]', new FakeElement({textContent: detailText}));
  row.children.set(
    '[data-catalog-sync-retry]',
    new FakeElement({dataset: {catalogSyncRetry: source}, hidden: status !== 'failed', textContent: '重試'}),
  );
  return row;
}

function createHarness() {
  const rows = [
    makeRow('local', 'complete', '11 筆'),
    makeRow('bookmarks', 'failed', 'bookmark source failed'),
    makeRow('resources', 'failed', 'resource source failed'),
  ];
  const reindex = new FakeElement();
  const requests = [];
  const intervals = new Map();
  let nextInterval = 1;
  let resolvePost;
  const postResponse = new Promise((resolve) => { resolvePost = resolve; });
  const request = async (url, options = {}) => {
    requests.push({url, options});
    if (url === '/api/catalog/sync/status') {
      return {
        ok: true,
        json: async () => ({
          sources: {
            bookmarks: {status: 'retrying', next_attempt: 2, max_attempts: 4},
            resources: {status: 'syncing', attempt: 1, max_attempts: 4},
          },
        }),
      };
    }
    return postResponse;
  };
  const location = {
    href: '/navigator/?scope=all&q=motion&source=bookmarks&tags=design&sort=title',
    reloadCount: 0,
    reload() { this.reloadCount += 1; },
  };
  const document = {
    getElementById: (id) => (id === 'catalog-reindex' ? reindex : null),
    querySelectorAll: (selector) => (
      selector === '[data-catalog-sync-source]' ? rows : []
    ),
  };
  const window = {
    document,
    fetch: request,
    location,
    setInterval(callback) {
      const id = nextInterval;
      nextInterval += 1;
      intervals.set(id, callback);
      return id;
    },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(callback) { callback(); },
  };
  vm.runInNewContext(controllerSource, {window});
  return {
    rows,
    reindex,
    requests,
    intervals,
    resolvePost,
    location,
    controller: window.FlowinoneCatalogSync.controller,
  };
}

test('live Eagle progress renders processed and total counts', () => {
  const harness = createHarness();
  const localRow = harness.rows[0];
  harness.controller.renderSyncState('local', {
    status: 'syncing',
    attempt: 1,
    max_attempts: 4,
    processed: 125,
    total: 1000,
  });
  assert.equal(
    localRow.querySelector('[data-catalog-sync-detail]').textContent,
    '125/1000 筆 · 第 1/4 次嘗試',
  );
});

test('full rescan action requests the explicit fallback mode', async () => {
  const harness = createHarness();
  const syncPromise = harness.reindex.trigger('click');
  assert.deepEqual(
    JSON.parse(harness.requests.find(({url}) => url === '/api/catalog/sync').options.body),
    {sources: ['local', 'bookmarks', 'resources'], full_rescan: true},
  );
  harness.resolvePost({
    ok: true,
    json: async () => ({
      local: {status: 'complete', count: 11},
      bookmarks: {status: 'complete', count: 7},
      resources: {status: 'complete', count: 5},
    }),
  });
  await syncPromise;
  assert.equal(harness.location.reloadCount, 1);
});

test('retry posts and polls only one failed source while preserving filters', async () => {
  const harness = createHarness();
  const bookmarkRow = harness.rows[1];
  const resourcesRow = harness.rows[2];
  const bookmarkRetry = bookmarkRow.querySelector('[data-catalog-sync-retry]');
  const resourcesRetry = resourcesRow.querySelector('[data-catalog-sync-retry]');
  const originalUrl = harness.location.href;
  const retryPromise = bookmarkRetry.trigger('click');

  assert.equal(bookmarkRetry.disabled, true);
  assert.equal(bookmarkRetry.textContent, '重試中…');
  assert.equal(resourcesRetry.disabled, true);
  assert.equal(harness.reindex.disabled, true);
  assert.deepEqual(
    JSON.parse(harness.requests.find(({url}) => url === '/api/catalog/sync').options.body),
    {sources: ['bookmarks']},
  );

  await [...harness.intervals.values()][0]();
  assert.equal(bookmarkRow.dataset.status, 'retrying');
  assert.match(
    bookmarkRow.querySelector('[data-catalog-sync-detail]').textContent,
    /2\/4/,
  );
  assert.equal(resourcesRow.dataset.status, 'failed');
  assert.equal(
    resourcesRow.querySelector('[data-catalog-sync-detail]').textContent,
    'resource source failed',
  );

  harness.resolvePost({
    ok: true,
    json: async () => ({bookmarks: {status: 'complete', count: 7}}),
  });
  await retryPromise;

  assert.equal(bookmarkRow.dataset.status, 'complete');
  assert.equal(
    bookmarkRow.querySelector('[data-catalog-sync-detail]').textContent,
    '7 筆',
  );
  assert.equal(bookmarkRetry.hidden, true);
  assert.equal(harness.reindex.disabled, false);
  assert.equal(harness.intervals.size, 0);
  assert.equal(harness.location.href, originalUrl);
  assert.equal(harness.location.reloadCount, 0);
  assert.equal(
    harness.requests.filter(({url}) => url === '/api/catalog/sync').length,
    1,
  );
});

test('retry leaves a returned final error visible and retryable', async () => {
  const harness = createHarness();
  const bookmarkRow = harness.rows[1];
  const bookmarkRetry = bookmarkRow.querySelector('[data-catalog-sync-retry]');
  const retryPromise = bookmarkRetry.trigger('click');
  harness.resolvePost({
    ok: true,
    json: async () => ({
      bookmarks: {status: 'failed', error: 'bookmark parser still unavailable'},
    }),
  });
  await retryPromise;

  assert.equal(bookmarkRow.dataset.status, 'failed');
  assert.equal(
    bookmarkRow.querySelector('[data-catalog-sync-detail]').textContent,
    'bookmark parser still unavailable',
  );
  assert.equal(bookmarkRetry.hidden, false);
  assert.equal(bookmarkRetry.disabled, false);
  assert.equal(bookmarkRetry.textContent, '重試');
});

test('queued sync follows its durable job and renders the final source state', async () => {
  const row = makeRow('bookmarks', 'idle', '—');
  const requests = [];
  const request = async (url) => {
    requests.push(url);
    if (url === '/api/catalog/sync') {
      return {
        ok: true,
        json: async () => ({job: {id: 'job-1', status: 'pending'}}),
      };
    }
    if (url === '/api/catalog/sync/jobs/job-1') {
      return {
        ok: true,
        json: async () => ({job: {id: 'job-1', status: 'complete'}}),
      };
    }
    return {
      ok: true,
      json: async () => ({sources: {bookmarks: {status: 'complete', item_count: 9}}}),
    };
  };
  const root = {
    getElementById: () => null,
    querySelectorAll: (selector) => (
      selector === '[data-catalog-sync-source]' ? [row] : []
    ),
  };
  const timers = {
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout(callback) { callback(); },
  };
  const context = {window: {document: null, fetch: request, location: {}, ...timers}};
  vm.runInNewContext(controllerSource, context);
  const controller = context.window.FlowinoneCatalogSync.createCatalogSyncController({
    root, request, timers, location: {},
  });

  await controller.runSync(['bookmarks']);

  assert.deepEqual(requests, [
    '/api/catalog/sync',
    '/api/catalog/sync/jobs/job-1',
    '/api/catalog/sync/status',
  ]);
  assert.equal(row.dataset.status, 'complete');
  assert.equal(row.querySelector('[data-catalog-sync-detail]').textContent, '9 筆');
});
