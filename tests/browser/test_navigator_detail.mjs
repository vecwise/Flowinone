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
  pause() { this.paused = true; }
  focus(options) { this.focused = true; this.focusOptions = options; }
  click(event = {}) { return this.listeners.click?.(event); }
}

function harness(firstItem = {}) {
  const names = [
    'status', 'content', 'image', 'video', 'source', 'folder', 'title', 'description', 'tags',
    'launch', 'read', 'related', 'back', 'close', 'people', 'people-group', 'folders', 'folders-group',
  ];
  const parts = Object.fromEntries(names.map((name) => [name, new Element()]));
  const dialog = new Element();
  dialog.querySelector = (selector) => parts[selector.replace('[data-gallery-detail-', '').replace(']', '')];
  dialog.showModal = () => { dialog.open = true; window.scrollY = 362; };
  dialog.close = () => { dialog.open = false; dialog.listeners.close?.(); };
  const link = new Element();
  link.closest = () => ({dataset: {navigatorItem: 'first'}});
  const page = new Element({navigatorQueryPayload: JSON.stringify({scope: 'gallery', sources: ['bookmarks'], q: 'old', media: 'video', tags: ['design'], tag_mode: 'all', favorite: true})});
  const document = {
    querySelector: (selector) => selector === '[data-gallery-detail]' ? dialog : page,
    querySelectorAll: () => [link],
    createElement: () => new Element(),
  };
  const requests = [];
  const items = {
    first: {id: 'first', title: 'First', item_type: 'bookmark', launch_source: 'bookmarks', launch_source_label: '書籤', launch_uri: 'https://example.test/first', target_blank: true, folder_path: 'Research / Art', tags: ['design']},
    second: {id: 'second', title: 'Second', item_type: 'video', launch_source: 'local', launch_source_label: '本機', launch_uri: '/video/second', playback_uri: '/catalog/items/second/video?source=local', tags: ['design']},
  };
  Object.assign(items.first, firstItem);
  const window = {
    document,
    scrollY: 464,
    scrollTo({top}) { this.scrollY = top; },
    location: {href: 'http://localhost/navigator/?scope=gallery&source=bookmarks&q=old'},
    fetch: async (url, options) => {
      requests.push({url, options});
      const id = url.includes('/second') ? 'second' : 'first';
      const payload = options ? {} : url.includes('/related')
        ? {items: id === 'first' ? [items.second] : []}
        : items[id];
      return {ok: true, json: async () => payload};
    },
  };
  vm.runInNewContext(source, {window, URL, URLSearchParams});
  return {parts, dialog, link, requests, window};
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
  assert.equal(new URL(parts.folder.href, 'http://localhost').searchParams.get('folder'), 'Research / Art');
  assert.equal(new URL(parts.tags.children[0].href, 'http://localhost').searchParams.get('tags'), 'design');
  assert.equal(parts.related.children.length, 1);
  assert.match(requests[0].url, /source=bookmarks/);
  const relatedUrl = new URL(requests.find((request) => request.url.includes('/related')).url, 'http://localhost');
  assert.equal(relatedUrl.searchParams.get('scope'), 'gallery');
  assert.equal(relatedUrl.searchParams.get('q'), 'old');
  assert.equal(relatedUrl.searchParams.get('media'), 'video');
  assert.equal(relatedUrl.searchParams.get('tags'), 'design');
  assert.equal(relatedUrl.searchParams.get('tag_mode'), 'all');
  assert.equal(relatedUrl.searchParams.get('favorite'), '1');

  parts.related.children[0].click();
  await flush();
  assert.equal(parts.title.textContent, 'Second');
  assert.equal(parts.video.src, '/catalog/items/second/video?source=local');
  assert.equal(parts.video.hidden, false);
  assert.equal(parts.image.hidden, true);
  assert.equal(parts.back.hidden, false);
  parts.back.click();
  await flush();
  assert.equal(parts.title.textContent, 'First');
  assert.equal(parts.video.hidden, true);
  assert.equal(parts.video.src, undefined);
  parts.close.click();
  assert.equal(dialog.open, false);
  assert.equal(link.focused, true);
});

test('resource launch from detail keeps the Navigator return context', async () => {
  const {parts, link} = harness({launch_uri: '/resources/resource-1/', launch_source: 'resources', target_blank: false});
  link.click({button: 0, preventDefault() {}});
  await flush();
  const destination = new URL(parts.launch.href, 'http://localhost');
  assert.equal(destination.pathname, '/resources/resource-1/');
  assert.equal(destination.searchParams.get('return_to'), '/navigator/?scope=gallery&source=bookmarks&q=old');
});

test('a merged bookmark opens its readable Resource with the return context', async () => {
  const {parts, link, requests} = harness({resource_detail_uri: '/resources/resource-1/'});
  link.click({button: 0, preventDefault() {}});
  await flush();
  assert.equal(parts.read.hidden, false);
  assert.equal(parts.launch.href, 'https://example.test/first');
  const destination = new URL(parts.read.href, 'http://localhost');
  assert.equal(destination.pathname, '/resources/resource-1/');
  assert.equal(destination.searchParams.get('return_to'), '/navigator/?scope=gallery&source=bookmarks&q=old');
  parts.read.click();
  assert.equal(JSON.parse(requests.at(-1).options.body).event_type, 'open');
});


test('opening and closing a preview keeps the gallery position despite native dialog focus scrolling', async () => {
  const {parts, link, window} = harness();
  link.click({button: 0, preventDefault() {}});
  await flush();
  assert.equal(window.scrollY, 464);
  assert.equal(parts.close.focusOptions.preventScroll, true);
  parts.close.click();
  assert.equal(link.focusOptions.preventScroll, true);
  assert.equal(window.scrollY, 464);
});

test('typed destinations switch sets, carry display choices, and reset when another item opens', async () => {
  const browseTargets = [
    {kind: 'tag', label: '木質 & warm:色', url: '/navigator/?scope=all&source=eagle&target=tag%3Atag-id'},
    {kind: 'person', label: 'A <name>', url: '/navigator/?scope=all&source=eagle&target=person%3Aperson-id'},
    {kind: 'eagle_folder', label: '室內參考', url: '/navigator/?scope=all&source=eagle&target=eagle_folder%3Alibrary%3Afolder-id'},
  ];
  const {parts, link, window} = harness({browse_targets: browseTargets});
  window.location.href = 'http://localhost/navigator/?q=old&tags=old&target=person:old&cursor=old&view=list&shape=square';
  link.click({button: 0, preventDefault() {}});
  await flush();
  assert.equal(parts.tags.children[0].textContent, '木質 & warm:色');
  assert.equal(parts.people.children[0].textContent, 'A <name>');
  assert.equal(parts['people-group'].hidden, false);
  assert.equal(parts['folders-group'].hidden, false);
  for (const [container, expected] of [[parts.tags, 'tag:tag-id'], [parts.people, 'person:person-id'], [parts.folders, 'eagle_folder:library:folder-id']]) {
    const destination = new URL(container.children[0].href, 'http://localhost');
    assert.equal(destination.pathname, '/navigator/');
    assert.equal(destination.searchParams.get('target'), expected);
    assert.equal(destination.searchParams.get('source'), 'eagle');
    assert.equal(destination.searchParams.get('view'), 'list');
    assert.equal(destination.searchParams.get('shape'), 'square');
    ['q', 'tags', 'cursor', 'folder'].forEach((key) => assert.equal(destination.searchParams.has(key), false));
  }
  parts.related.children[0].click();
  await flush();
  assert.equal(parts['people-group'].hidden, true);
  assert.equal(parts['folders-group'].hidden, true);
  assert.equal(parts.people.children.length, 0);
  assert.equal(parts.folders.children.length, 0);
});
