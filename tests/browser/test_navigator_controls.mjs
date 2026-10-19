import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/navigator_controls.js', import.meta.url), 'utf8');
const bind = (values = {}) => ({...values, addEventListener(name, fn) { this[name] = fn; }});
const applied = {q: 'Design', sources: ['local', 'bookmarks'], tags: ['art', 'ux'], media: 'image', type: '', folder: 'Art', favorite: true, unviewed: true, duration_min: 2, duration_max: 30, added_from: '2026-01-01', sort: 'recently_added', seed: 8, cursor: 'old', limit: 2};
function harness({query = applied, href, storage = new Map(), blockedStorage = false} = {}) {
  const search = {value: query.q};
  const sort = bind({value: query.sort});
  const form = bind();
  const reshuffle = bind();
  const page = {dataset: {navigatorQueryPayload: JSON.stringify(query)}};
  const location = {href: href || 'http://localhost/navigator/?q=Design&source=local&source=bookmarks&tags=art&tags=ux&media=image&folder=Art&favorite=1&unviewed=1&duration_min=2&duration_max=30&added_from=2026-01-01&sort=recently_added&seed=8&cursor=old&resume_scroll=700&context=saved&limit=2&view=list&shape=square&ui=curator#item-old', assign(value) { this.destination = value; }};
  const selectors = {'[data-catalog-sort]': sort, '#navigator-q': search, 'form.navigator-query': form, '[data-navigator-query-payload]': page, '[data-catalog-reshuffle]': reshuffle};
  const sessionStorage = {getItem(key) { if (blockedStorage) throw Error('blocked'); return storage.get(key); }, removeItem(key) { storage.delete(key); }, setItem(key, value) { if (blockedStorage) throw Error('blocked'); storage.set(key, value); }};
  const window = {document: {querySelector: (s) => selectors[s]}, location, sessionStorage};
  vm.runInNewContext(source, {window, URL});
  return {sort, search, form, reshuffle, storage, location};
}

test('sort immediately navigates the applied full query, preserving repeats and presentation, not draft text', () => {
  const state = harness();
  state.search.value = 'Not submitted';
  state.sort.value = 'title';
  state.sort.change();
  const target = new URL(state.location.destination);
  assert.equal(target.searchParams.get('q'), 'Design');
  assert.equal(target.searchParams.get('sort'), 'title');
  assert.equal(target.searchParams.get('resume_scroll'), '0');
  assert.deepEqual(target.searchParams.getAll('source'), ['local', 'bookmarks']);
  assert.deepEqual(target.searchParams.getAll('tags'), ['art', 'ux']);
  for (const key of ['media', 'folder', 'favorite', 'unviewed', 'duration_min', 'duration_max', 'added_from', 'limit', 'view', 'shape', 'ui']) assert.equal(target.searchParams.get(key), new URL(state.location.href).searchParams.get(key));
  for (const key of ['cursor', 'seed', 'context']) assert.equal(target.searchParams.has(key), false);
  assert.equal(target.hash, '');
  const next = harness({query: {...applied, sort: 'title', cursor: '', seed: 0}, href: target.href, storage: state.storage});
  assert.equal(next.search.value, 'Not submitted');
  assert.equal(next.storage.size, 0);
});

test('random seed redirects restore the draft once, pagination does not reuse it, and reshuffle drops old seed', () => {
  const state = harness();
  state.search.value = 'Draft';
  state.sort.value = 'random';
  state.sort.change();
  const destination = new URL(state.location.destination);
  destination.searchParams.set('seed', '123');
  const next = harness({query: {...applied, sort: 'random', cursor: '', seed: 123}, href: destination.href, storage: state.storage});
  assert.equal(next.search.value, 'Draft');
  const paged = harness({query: {...applied, sort: 'random', seed: 123, cursor: 'next'}, storage: next.storage});
  assert.equal(paged.search.value, 'Design');
  let prevented = false;
  next.reshuffle.click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(new URL(next.location.destination).searchParams.has('seed'), false);
});

test('drafts are discarded after unrelated filters, and storage denial never prevents sorting', () => {
  const state = harness();
  state.search.value = 'Draft';
  state.sort.value = 'title';
  state.sort.change();
  const other = harness({query: {...applied, q: 'Other', sort: 'title'}, storage: state.storage});
  assert.equal(other.search.value, 'Other');
  assert.equal(other.storage.size, 0);
  const blocked = harness({blockedStorage: true});
  blocked.sort.value = 'title';
  blocked.sort.change();
  assert.equal(new URL(blocked.location.destination).searchParams.get('sort'), 'title');
});
