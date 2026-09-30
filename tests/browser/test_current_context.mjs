import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/current_context.js', import.meta.url), 'utf8');

function harness(clipboard) {
  const button = {addEventListener(_name, callback) { this.click = callback; }};
  const input = {value: 'http://localhost/navigator/views/example/', focus() { this.focused = true; }, select() { this.selected = true; }};
  const status = {textContent: ''};
  const section = {querySelector(selector) {
    if (selector === '[data-copy-context]') return button;
    if (selector === '[data-context-share-url]') return input;
    return status;
  }};
  const document = {querySelectorAll: () => [section]};
  vm.runInNewContext(source, {document, navigator: {clipboard}});
  return {button, input, status};
}

test('current view copies its share link', async () => {
  let copied;
  const {button, input, status} = harness({writeText: async (value) => { copied = value; }});
  await button.click();
  assert.equal(copied, input.value);
  assert.equal(status.textContent, '已複製視角連結');
});

test('current view selects its link when Clipboard API is unavailable', async () => {
  const {button, input, status} = harness(undefined);
  await button.click();
  assert.equal(input.selected, true);
  assert.match(status.textContent, /Ctrl\+C/);
});
