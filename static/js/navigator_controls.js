(function initializeCatalogControls(global) {
  'use strict';
  const DRAFT_KEY = 'flowinone-sort-draft';
  function createCatalogControls(root = global.document) {
    const sort = root?.querySelector('[data-catalog-sort]');
    const search = root?.querySelector('#navigator-q');
    if (!sort || !search) return null;
    const queryForm = root.querySelector('form.navigator-query');
    const page = root.querySelector('[data-navigator-query-payload]');
    const identity = () => {
      const query = JSON.parse(page.dataset.navigatorQueryPayload);
      ['sort', 'seed', 'cursor'].forEach((key) => delete query[key]);
      return JSON.stringify(query);
    };
    // A draft belongs only to the immediately following sort navigation in this tab.
    try {
      const draft = JSON.parse(global.sessionStorage.getItem(DRAFT_KEY) || 'null');
      global.sessionStorage.removeItem(DRAFT_KEY);
      if (draft && draft.identity === identity() && draft.sort === sort.value) search.value = draft.value;
    } catch (_) { /* Storage is optional. */ }
    const navigate = (value, reshuffle = false) => {
      const url = new URL(global.location.href);
      if (!reshuffle && value === url.searchParams.get('sort')) return;
      url.searchParams.set('sort', value);
      ['cursor', 'resume_scroll', 'context', 'seed'].forEach((key) => url.searchParams.delete(key));
      url.hash = '';
      // Explicit zero also overrides an earlier saved position for this sort order.
      url.searchParams.set('resume_scroll', '0');
      try {
        global.sessionStorage.setItem(DRAFT_KEY, JSON.stringify({
          identity: identity(), sort: value, value: search.value,
        }));
      } catch (_) { /* The query still works without draft storage. */ }
      global.location.assign(url.href);
    };
    sort.addEventListener('change', () => navigate(sort.value));
    root.querySelector('[data-catalog-reshuffle]')?.addEventListener('click', (event) => {
      event.preventDefault();
      navigate('random', true);
    });
    queryForm?.addEventListener('submit', () => {
      try { global.sessionStorage.removeItem(DRAFT_KEY); } catch (_) { /* optional */ }
    });
    return {navigate};
  }
  global.FlowinoneCatalogControls = {createCatalogControls};
  if (global.document) global.FlowinoneCatalogControls.controller = createCatalogControls();
}(window));
