(function initializeNavigatorViews(global) {
  'use strict';

  const STORAGE_KEY = 'flowinone-gallery-view';
  const VIEWS = new Set(['grid', 'list', 'shelf']);

  function createNavigatorViews(root = global.document) {
    const gallery = root?.querySelector('[data-gallery-view]');
    const buttons = [...(root?.querySelectorAll('[data-gallery-view-option]') || [])];
    if (!gallery || !buttons.length) return null;

    const setView = (view, persist = true) => {
      if (!VIEWS.has(view)) return;
      gallery.dataset.galleryView = view;
      buttons.forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.galleryViewOption === view));
      });
      if (persist) {
        try { global.localStorage.setItem(STORAGE_KEY, view); } catch (_) { /* optional preference */ }
      }
    };

    let initial = 'grid';
    try { initial = global.localStorage.getItem(STORAGE_KEY) || 'grid'; } catch (_) { /* private browsing */ }
    setView(VIEWS.has(initial) ? initial : 'grid', false);
    buttons.forEach((button) => {
      button.addEventListener('click', () => setView(button.dataset.galleryViewOption));
    });
    return {setView};
  }

  global.FlowinoneNavigatorViews = {createNavigatorViews};
  if (global.document) global.FlowinoneNavigatorViews.controller = createNavigatorViews();
}(window));
