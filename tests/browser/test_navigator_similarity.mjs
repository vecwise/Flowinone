import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_similarity.js', import.meta.url), 'utf8');
const element = () => ({textContent: '', hidden: false, addEventListener(name, handler) { this[name] = handler; }});

test('browse cards can find similar images without management controls', async () => {
  const summary = element(), results = element(), empty = element(), button = element();
  button.dataset = {catalogSimilarItem: 'image-one'};
  const nodes = {'[data-catalog-similar-summary]': summary, '[data-catalog-similar-results]': results, '[data-catalog-similar-empty]': empty};
  const dialog = {querySelector: selector => nodes[selector], showModal() { this.open = true; }};
  const requests = [];
  const window = {
    document: {querySelector: selector => selector === '[data-catalog-similar-dialog]' ? dialog : null,
      getElementById: () => null, querySelectorAll: () => [button]},
    fetch: async url => { requests.push(url); return {ok: true, json: async () => ({analyzed: false, items: []})}; },
  };
  vm.runInNewContext(source, {window});
  await button.click();
  assert.equal(dialog.open, true);
  assert.deepEqual(requests, ['/api/catalog/items/image-one/similar-images']);
  assert.match(summary.textContent, /尚未分析/);
  assert.equal(empty.hidden, false);
});

test('management can queue image analysis without a browse dialog', async () => {
  const summary = element(), detail = element(), button = element();
  const panel = {querySelector: selector => selector.includes('summary') ? summary : detail};
  const requests = [];
  const window = {
    document: {querySelector: selector => selector === '[data-catalog-similarity-status]' ? panel : null,
      getElementById: () => button, querySelectorAll: () => []},
    fetch: async (url, options) => { requests.push([url, options]); return {ok: true, json: async () => ({analyzed_items: 3})}; },
  };
  vm.runInNewContext(source, {window});
  await button.click();
  assert.equal(requests[0][0], '/api/catalog/similarity/rebuild');
  assert.equal(requests[0][1].method, 'POST');
  assert.equal(requests[1][0], '/api/catalog/similarity/status');
  assert.match(summary.textContent, /3 張/);
  assert.equal(button.disabled, false);
});
