(function initializeBrowsePresentation(global) {
  'use strict';
  const shapes = ['auto', 'landscape', 'portrait', 'square'];
  const isBrowsePath = (path) => path === '/navigator/' || path.startsWith('/navigator/views/')
    || /^\/(both|folders|chrome|chrome_youtube|EAGLE_folders|EAGLE_tags|EAGLE_smart_folders|EAGLE_smart_folder|EAGLE_folder|EAGLE_tag|EAGLE_stream)\//.test(path);

  function createBrowsePresentation(root = global.document) {
    const grid = root?.querySelector('[data-gallery-shape]');
    const select = root?.querySelector('[data-gallery-shape-option]');
    if (!grid || !select) return null;
    const applyParam = (url, shape) => {
      if (shape === 'auto') url.searchParams.delete('shape');
      else url.searchParams.set('shape', shape);
    };
    const setShape = (value) => {
      const shape = shapes.includes(value) ? value : 'auto';
      grid.dataset.galleryShape = shape;
      select.value = shape;
      const url = new URL(global.location.href);
      applyParam(url, shape);
      global.history.replaceState(global.history.state, '', url);
      const input = root.querySelector('[data-shape-input]');
      if (input) input.value = shape;
      const hint = root.querySelector('[data-gallery-shape-hint]');
      if (hint) {
        const scope = grid.dataset.mediaScope;
        hint.textContent = shape === 'auto'
          ? scope === 'video' ? '影片 16:9'
            : ['image', 'album'].includes(scope) ? '直式 3:4'
              : '混合 4:3'
          : '不影響篩選';
      }
      root.querySelectorAll('a[href]').forEach((link) => {
        if (link.hasAttribute('data-ui-fixed')) return;
        const target = new URL(link.href, global.location.href);
        if (target.origin !== url.origin) return;
        if (target.searchParams.has('return_to')) {
          const back = new URL(target.searchParams.get('return_to'), url);
          if (back.origin !== url.origin || !isBrowsePath(back.pathname)) return;
          applyParam(back, shape);
          target.searchParams.set('return_to', back.pathname + back.search + back.hash);
        } else if (isBrowsePath(target.pathname) || link.hasAttribute('data-browse-link')) {
          applyParam(target, shape);
        } else return;
        link.href = target.pathname + target.search + target.hash;
      });
    };
    setShape(new URL(global.location.href).searchParams.get('shape') || 'auto');
    select.addEventListener('change', () => setShape(select.value));
    root.querySelectorAll('[data-media-filter]').forEach((radio) => {
      radio.addEventListener('change', () => {
        const type = root.querySelector('#navigator-type');
        if (type) type.value = '';
        radio.form.requestSubmit();
      });
    });
    return {setShape};
  }
  global.FlowinoneBrowsePresentation = {createBrowsePresentation};
  if (global.document) global.FlowinoneBrowsePresentation.controller = createBrowsePresentation();
}(window));
