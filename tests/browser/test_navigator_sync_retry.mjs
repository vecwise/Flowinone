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
  return {rows, reindex, requests, intervals, resolvePost, location};
}

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
