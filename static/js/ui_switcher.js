(function initializeUI(global) {
  'use strict';
  const KEYS = ['baseline', 'curator', 'reader', 'workbench'];
  const normalize = (key) => KEYS.includes(key) ? key : 'baseline';
  const isPagePath = (path) => path === '/' || path === '/search' || path === '/item_db'
    || /^\/(navigator(?:\/views\/[^/]+)?|library|route-guide|ui-lab|folders|both(?:\/.*)?|grid(?:\/.*)?|slide(?:\/.*)?|chrome(?:\/.*)?|chrome_youtube|image\/.*|video\/.*|catalog\/duplicate-review|EAGLE_(?:folders|folder\/[^/]+|tags|tag\/[^/]+|smart_folders|smart_folder\/[^/]+|stream|image\/[^/]+|video\/[^/]+))\/?$/.test(path)
    || /^\/resources\/(?:[^/]+\/(?:versions\/)?)?$/.test(path);

  function withUI(value, key, base, depth = 0) {
    if (!value || value.startsWith('#') || value.startsWith('//') || value.includes('\\')) return value;
    const destination = new URL(value, base);
    if (destination.origin !== new URL(base).origin || !isPagePath(destination.pathname)) return value;
    const returnTo = destination.searchParams.get('return_to');
    if (returnTo && depth < 4) destination.searchParams.set('return_to', withUI(returnTo, key, base, depth + 1));
    destination.searchParams.set('ui', normalize(key));
    return destination.pathname + destination.search + destination.hash;
  }

  function createUISwitcher(root = global.document) {
    if (!root?.documentElement) return null;
    let selected = normalize(root.documentElement.dataset.ui);
    let active = new URL(global.location.href).searchParams.has('ui');
    const base = () => global.location.href;

    function decorateLink(link) {
      if (link.hasAttribute('data-ui-fixed') || link.hasAttribute('download')) return;
      const labEntry = link.hasAttribute('data-ui-lab-entry');
      if (!active && !labEntry) return;
      const previous = link.getAttribute('href');
      let next = active ? withUI(previous, selected, base()) : previous;
      if (labEntry && new URL(base()).pathname !== '/ui-lab/') {
        const destination = new URL(next, base());
        const current = new URL(base());
        destination.searchParams.set('return_to', current.pathname + current.search + current.hash);
        next = destination.pathname + destination.search + destination.hash;
      }
      if (next !== previous) link.setAttribute('href', next);
    }

    function decorateForm(form) {
      if (!active || form.getAttribute('method') === 'dialog'
          || (!form.hasAttribute('action') && !form.hasAttribute('method'))) return;
      const destination = new URL(form.getAttribute('action') || base(), base());
      if (destination.origin !== new URL(base()).origin || destination.pathname.startsWith('/api/')) return;
      let field = form.querySelector('input[name="ui"]');
      if (!field) {
        field = root.createElement('input');
        field.type = 'hidden'; field.name = 'ui';
        form.appendChild(field);
      }
      field.value = selected;
      // Form data wins over stale query parameters when the UI changes in place.
      if (destination.searchParams.has('ui')) {
        destination.searchParams.set('ui', selected);
        const action = destination.pathname + destination.search + destination.hash;
        if (form.getAttribute('action') !== action) form.setAttribute('action', action);
      }
    }

    function refresh(scope = root) {
      scope.querySelectorAll('a[href]').forEach(decorateLink);
      scope.querySelectorAll('form').forEach(decorateForm);
      if (active) scope.querySelectorAll('[data-context-share-url]').forEach((input) => {
        input.value = new URL(withUI(input.value, selected, base()), base()).href;
      });
    }

    function apply(key, updateURL = true) {
      selected = normalize(key);
      active = true;
      // The DOM stays mounted: draft text, file inputs, dialogs and media survive.
      root.documentElement.dataset.ui = selected;
      root.querySelectorAll('[data-ui-select]').forEach((select) => { select.value = selected; });
      root.querySelectorAll('[data-ui-current]').forEach((badge) => {
        badge.hidden = badge.dataset.uiCurrent !== selected;
      });
      if (updateURL) {
        const url = new URL(base());
        url.searchParams.set('ui', selected);
        global.history.replaceState(global.history.state, '', url.pathname + url.search + url.hash);
      }
      refresh();
      const select = root.querySelector('[data-ui-select]');
      root.querySelectorAll('[data-ui-status]').forEach((status) => {
        status.textContent = `已切換為${select?.selectedOptions[0]?.textContent || selected}，目前條件與輸入已保留。`;
      });
    }

    root.querySelectorAll('[data-ui-select]').forEach((select) => {
      select.addEventListener('change', () => apply(select.value));
    });
    root.querySelectorAll('[data-ui-apply]').forEach((button) => {
      button.addEventListener('click', () => apply(button.dataset.uiApply));
    });
    root.addEventListener('click', (event) => {
      const link = event.target.closest('a[href]');
      if (link) decorateLink(link);
    }, true);
    root.addEventListener('submit', (event) => decorateForm(event.target), true);
    global.addEventListener('popstate', () => {
      apply(new URL(base()).searchParams.get('ui'), false);
    });
    if (global.MutationObserver) {
      new global.MutationObserver((records) => {
        records.forEach((record) => {
          if (record.type === 'attributes') {
            if (record.target.tagName === 'A') decorateLink(record.target);
            if (record.target.tagName === 'FORM') decorateForm(record.target);
          } else record.addedNodes.forEach((node) => {
            if (node.nodeType !== 1) return;
            if (node.matches('a[href]')) decorateLink(node);
            if (node.matches('form')) decorateForm(node);
            refresh(node);
          });
        });
      }).observe(root.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['href', 'action']});
    }
    refresh();
    return {apply, refresh};
  }

  global.FlowinoneUI = {withUI, isPagePath, createUISwitcher};
  if (global.document) global.FlowinoneUI.controller = createUISwitcher();
}(window));
