(function initializeNavigatorCommands(global) {
  'use strict';

  function createNavigatorCommands(root = global.document) {
    const palette = root.querySelector('[data-command-palette]');
    const saveDialog = root.querySelector('[data-save-search-dialog]');
    const search = root.getElementById('navigator-q');
    const status = root.getElementById('navigator-action-status');
    const queryNode = root.querySelector('[data-navigator-query]');
    if (!palette || !saveDialog || !search || !queryNode) return null;

    const query = JSON.parse(queryNode.dataset.navigatorQueryPayload || '{}');
    const paletteInput = palette.querySelector('[data-command-palette-input]');
    const commandItems = [...palette.querySelectorAll('[data-command-item]')];
    const empty = palette.querySelector('[data-command-palette-empty]');
    const saveForm = saveDialog.querySelector('[data-save-search-form]');
    const label = saveDialog.querySelector('[name="label"]');
    const feedback = saveDialog.querySelector('[data-save-search-feedback]');
    let previousFocus = null;

    const show = (dialog, focusTarget) => {
      previousFocus = root.activeElement;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
      global.requestAnimationFrame(() => focusTarget?.focus());
    };
    const close = (dialog) => {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
      previousFocus?.focus?.();
    };
    const filterCommands = () => {
      const needle = paletteInput.value.trim().toLocaleLowerCase();
      let visible = 0;
      commandItems.forEach((item) => {
        const matches = !needle || item.textContent.toLocaleLowerCase().includes(needle);
        item.hidden = !matches;
        visible += Number(matches);
      });
      empty.hidden = visible > 0;
    };
    const openPalette = () => {
      paletteInput.value = '';
      filterCommands();
      show(palette, paletteInput);
    };
    const openSaveDialog = () => {
      if (palette.open) close(palette);
      feedback.hidden = true;
      feedback.textContent = '';
      label.value = search.value ? `搜尋：${search.value}` : '';
      show(saveDialog, label);
    };

    root.querySelectorAll('[data-command-palette-open]').forEach((trigger) => {
      trigger.addEventListener('click', openPalette);
    });
    root.querySelectorAll('[data-save-search-open]').forEach((trigger) => {
      trigger.addEventListener('click', openSaveDialog);
    });
    root.querySelectorAll('[data-save-search-cancel]').forEach((trigger) => {
      trigger.addEventListener('click', () => close(saveDialog));
    });
    global.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault();
        openPalette();
      }
    });
    paletteInput.addEventListener('input', filterCommands);
    palette.querySelector('[data-command-action="focus-search"]')?.addEventListener('click', () => {
      close(palette);
      search.focus();
      search.select();
    });
    saveForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const name = label.value.trim();
      if (!name) {
        feedback.textContent = '請為這個搜尋命名。';
        feedback.hidden = false;
        label.focus();
        return;
      }
      const submit = saveForm.querySelector('[type="submit"]');
      submit.disabled = true;
      feedback.hidden = true;
      try {
        const response = await global.fetch('/api/catalog/saved-searches', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({label: name, query}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '無法儲存搜尋');
        status.textContent = `已儲存搜尋：${result.label}`;
        global.location.reload();
      } catch (error) {
        feedback.textContent = error.message;
        feedback.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });
    root.querySelectorAll('[data-delete-saved-search]').forEach((button) => {
      button.addEventListener('click', async () => {
        const row = button.closest('[data-saved-search-id]');
        const name = row?.querySelector('strong')?.textContent || '這個搜尋';
        if (!global.confirm(`要移除「${name}」嗎？`)) return;
        button.disabled = true;
        try {
          const response = await global.fetch(`/api/catalog/saved-searches/${button.dataset.deleteSavedSearch}`, {method: 'DELETE'});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || '無法移除儲存搜尋');
          row.remove();
          status.textContent = `已移除儲存搜尋：${name}`;
        } catch (error) {
          status.textContent = error.message;
          button.disabled = false;
        }
      });
    });

    return {openPalette, openSaveDialog, filterCommands};
  }

  global.FlowinoneNavigatorCommands = {createNavigatorCommands};
  if (global.document) {
    global.FlowinoneNavigatorCommands.controller = createNavigatorCommands();
  }
}(window));
