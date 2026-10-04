(function initializeNavigatorViews(global) {
  'use strict';

  const STORAGE_KEY = 'flowinone-gallery-view';
  const normalizeView = (view) => view === 'list' || view === 'knowledge' ? 'list' : 'grid';

  function createNavigatorViews(root = global.document) {
    const gallery = root?.querySelector('[data-gallery-view]');
    const buttons = [...(root?.querySelectorAll('[data-gallery-view-option]') || [])];
    if (!gallery || !buttons.length) return null;

    const setView = (view, persist = true) => {
      const normalized = normalizeView(view);
      gallery.dataset.galleryView = normalized;
      buttons.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.galleryViewOption === normalized));
      });
      if (persist) {
        try { global.localStorage.setItem(STORAGE_KEY, normalized); } catch (_) { /* optional preference */ }
      }
      const url = new URL(global.location.href);
      if (normalized === 'list') url.searchParams.set('view', 'list');
      else url.searchParams.delete('view');
      global.history.replaceState(null, '', url);
      const viewInput = root.querySelector('form.navigator-query input[name="view"]');
      if (viewInput) viewInput.value = normalized === 'list' ? 'list' : '';
      root.querySelectorAll('a[href]').forEach((link) => {
        const destination = new URL(link.href, global.location.href);
        if (destination.origin !== global.location.origin) return;
        if (destination.pathname.startsWith('/resources/') && destination.searchParams.has('return_to')) {
          const returnUrl = new URL(destination.searchParams.get('return_to'), global.location.href);
          if (returnUrl.origin !== global.location.origin || returnUrl.pathname !== '/navigator/') return;
          if (normalized === 'list') returnUrl.searchParams.set('view', 'list');
          else returnUrl.searchParams.delete('view');
          destination.searchParams.set('return_to', returnUrl.pathname + returnUrl.search + returnUrl.hash);
          link.href = destination.pathname + destination.search + destination.hash;
          return;
        }
        if (!(destination.pathname === '/navigator/' || destination.pathname.startsWith('/navigator/views/'))) return;
        if (normalized === 'list') destination.searchParams.set('view', 'list');
        else destination.searchParams.delete('view');
        link.href = destination.pathname + destination.search + destination.hash;
      });
    };

    let initial = new URL(global.location.href).searchParams.get('view');
    if (!initial) {
      try { initial = global.localStorage.getItem(STORAGE_KEY); } catch (_) { /* private browsing */ }
    }
    setView(normalizeView(initial), Boolean(initial));
    buttons.forEach((button) => {
      button.addEventListener('click', () => setView(button.dataset.galleryViewOption));
    });
    return {setView};
  }

  global.FlowinoneNavigatorViews = {createNavigatorViews};
  if (global.document) global.FlowinoneNavigatorViews.controller = createNavigatorViews();
}(window));
