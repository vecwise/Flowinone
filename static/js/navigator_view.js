(function initializeNavigatorViews(global) {
  'use strict';

  const STORAGE_KEY = 'flowinone-gallery-view';
  const VIEWS = new Set(['grid', 'list', 'shelf', 'knowledge']);

  function createNavigatorViews(root = global.document) {
    const gallery = root?.querySelector('[data-gallery-view]');
    const buttons = [...(root?.querySelectorAll('[data-gallery-view-option]') || [])];
    if (!gallery || !buttons.length) return null;

    const setView = (view, persist = true) => {
      if (!VIEWS.has(view)) return;
      if (view === 'knowledge' && gallery.dataset.knowledgeLoaded !== 'true') {
        const destination = new URL(global.location.href);
        destination.searchParams.set('view', 'knowledge');
        global.location.assign(destination.toString());
        return;
      }
      gallery.dataset.galleryView = view;
      buttons.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.galleryViewOption === view));
      });
      if (persist) {
        try { global.localStorage.setItem(STORAGE_KEY, view); } catch (_) { /* optional preference */ }
        const url = new URL(global.location.href);
        if (view === 'knowledge') url.searchParams.set('view', 'knowledge');
        else url.searchParams.delete('view');
        global.history.replaceState(null, '', url);
        const viewInput = root.querySelector('form.navigator-query input[name="view"]');
        if (viewInput) viewInput.value = view === 'knowledge' ? 'knowledge' : '';
        root.querySelectorAll('a[href]').forEach((link) => {
          const destination = new URL(link.href, global.location.href);
          if (destination.origin !== global.location.origin || destination.pathname !== '/navigator/') return;
          if (view === 'knowledge') destination.searchParams.set('view', 'knowledge');
          else destination.searchParams.delete('view');
          link.href = destination.pathname + destination.search + destination.hash;
        });
      }
    };

    let initial = new URL(global.location.href).searchParams.get('view') || 'grid';
    if (initial === 'grid') {
      try { initial = global.localStorage.getItem(STORAGE_KEY) || 'grid'; } catch (_) { /* private browsing */ }
    }
    setView(VIEWS.has(initial) ? initial : 'grid', false);
    buttons.forEach((button) => {
      button.addEventListener('click', () => setView(button.dataset.galleryViewOption));
    });
    return {setView};
  }

  global.FlowinoneNavigatorViews = {createNavigatorViews};
  if (global.document) global.FlowinoneNavigatorViews.controller = createNavigatorViews();
}(window));
