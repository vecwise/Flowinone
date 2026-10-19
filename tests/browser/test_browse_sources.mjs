import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script = fs.readFileSync(new URL('../../static/js/browse_sources.js', import.meta.url), 'utf8');

function harness(selected = ['bookmarks']) {
  const inputs = ['bookmarks', 'eagle', 'local', 'resources'].map(value => ({
    value, checked: selected.includes(value),
    addEventListener(name, handler) { this[name] = handler; },
  }));
  const returnTo = {value: '/navigator/?view=grid'};
  const hint = {textContent: ''};
  const pending = new Map();
  let nextTimer = 0;
  let submissions = 0;
  const form = {
    querySelectorAll: () => inputs,
    querySelector: () => returnTo,
    addEventListener(name, handler) { this[name] = handler; },
    setAttribute() {},
    requestSubmit() {
      let prevented = false;
      this.submit({preventDefault() { prevented = true; }});
      if (!prevented) submissions++;
    },
  };
  const location = {pathname: '/navigator/', search: '?view=list&shape=square&source=bookmarks', hash: '#item-selected'};
  vm.runInNewContext(script, {
    document: {querySelector: () => form, getElementById: () => hint},
    window: {location, addEventListener() {}},
    sessionStorage: {getItem() {}, removeItem() {}, setItem() {}},
    setTimeout(handler) { pending.set(++nextTimer, handler); return nextTimer; },
    clearTimeout(id) { pending.delete(id); },
  });
  return {inputs, form, returnTo, hint, pending, submissions: () => submissions,
    toggle(value) { const input = inputs.find(item => item.value === value); input.checked = !input.checked; input.change(); },
    flush() { for (const handler of [...pending.values()]) handler(); pending.clear(); },
  };
}

test('keyboard or mouse cannot deselect the last source or submit an empty filter', () => {
  const ui = harness();
  ui.toggle('bookmarks');
  ui.flush();
  assert.equal(ui.inputs[0].checked, true);
  assert.match(ui.hint.textContent, /至少保留一個/);
  assert.equal(ui.submissions(), 0);
});

test('rapid multi-selection submits once and preserves the live presentation URL', () => {
  const ui = harness(['bookmarks', 'eagle', 'local', 'resources']);
  ui.toggle('eagle');
  ui.toggle('resources');
  ui.flush();
  assert.deepEqual(ui.inputs.filter(item => item.checked).map(item => item.value), ['bookmarks', 'local']);
  assert.equal(ui.submissions(), 1);
  assert.equal(ui.returnTo.value, '/navigator/?view=list&shape=square&source=bookmarks#item-selected');
  ui.form.requestSubmit();
  assert.equal(ui.submissions(), 1);
});
