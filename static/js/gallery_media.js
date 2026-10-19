(function initializeGalleryMedia(global) {
  'use strict';
  const root = global.document;
  const gallery = root.querySelector('[data-gallery-view]');
  if (!gallery) return;
  const continuous = () => gallery.dataset.galleryView === 'list';
  const names = root.querySelector('[data-gallery-names-option]');
  const next = root.querySelector('[data-browse-next]');
  const status = root.querySelector('[data-browse-load-status]');
  const knownCards = new WeakSet();
  const loadedPages = [];
  const rememberedRatios = new Map();
  let loading = false;
  let failed = false;
  let restoring = false;
  let layoutFrame = 0;

  function updateShareLink() {
    const current = new URL(global.location.href);
    root.querySelectorAll('[data-context-share-url]').forEach((input) => {
      const url = new URL(input.value, current);
      ['view', 'shape', 'names'].forEach((key) => {
        if (current.searchParams.has(key)) url.searchParams.set(key, current.searchParams.get(key));
        else url.searchParams.delete(key);
      });
      input.value = url.href;
    });
  }

  function setNames(value) {
    const choice = ['auto', 'show', 'hide'].includes(value) ? value : 'auto';
    gallery.dataset.galleryNames = choice;
    if (names) names.value = choice;
    try { global.localStorage.setItem('flowinone-gallery-names', choice); } catch (_) { /* optional */ }
    const update = (url) => {
      if (choice === 'auto') url.searchParams.delete('names');
      else url.searchParams.set('names', choice);
    };
    const current = new URL(global.location.href);
    update(current);
    global.history.replaceState(global.history.state, '', current);
    root.querySelectorAll('a[href]').forEach((link) => {
      if (link.hasAttribute('data-ui-fixed')) return;
      const url = new URL(link.href, current);
      if (url.origin !== current.origin) return;
      if (url.searchParams.has('return_to')) {
        const back = new URL(url.searchParams.get('return_to'), current);
        if (back.origin !== current.origin) return;
        update(back);
        url.searchParams.set('return_to', back.pathname + back.search + back.hash);
      } else if (url.pathname === '/navigator/' || /^\/(both|folders|chrome|chrome_youtube|EAGLE_)/.test(url.pathname)) update(url);
      else return;
      link.href = url.pathname + url.search + url.hash;
    });
    const form = root.querySelector('form.navigator-query');
    if (form) {
      let input = form.querySelector('input[name="names"]');
      if (!input) {
        input = root.createElement('input'); input.type = 'hidden'; input.name = 'names'; form.append(input);
      }
      input.value = choice;
    }
    scheduleLayout();
    updateShareLink();
  }

  function layout() {
    layoutFrame = 0;
    gallery.classList.add('is-masonry-ready');
    const style = global.getComputedStyle(gallery);
    const row = parseFloat(style.gridAutoRows) || 4;
    const gap = parseFloat(style.rowGap) || 8;
    gallery.querySelectorAll('.gallery-card').forEach((card) => {
      const span = continuous() || card.hidden ? '' : `span ${Math.ceil((card.getBoundingClientRect().height + gap) / (row + gap))}`;
      if (card.style.gridRowEnd !== span) card.style.gridRowEnd = span;
    });
  }
  function scheduleLayout() {
    if (!layoutFrame) layoutFrame = global.requestAnimationFrame(layout);
  }
  const resize = new global.ResizeObserver(scheduleLayout);
  resize.observe(gallery);

  function loadMedia(media) {
    if (!continuous() || media.getAttribute('src')) return;
    media.hidden = false;
    media.parentElement.querySelector('[role="status"]').hidden = true;
    media.src = media.dataset.mediaSrc;
    if (media.tagName === 'VIDEO') { media.preload = 'metadata'; media.load(); }
  }
  const mediaObserver = new global.IntersectionObserver((entries) => {
    entries.forEach(({target, isIntersecting}) => { if (isIntersecting) loadMedia(target); });
  }, {rootMargin: '1000px 0px'});
  function bindCards() {
    gallery.querySelectorAll('.gallery-card').forEach((card) => {
      if (knownCards.has(card)) return;
      knownCards.add(card);
      resize.observe(card);
      const thumb = card.querySelector('.gallery-card-media img');
      const media = card.querySelector('[data-media-src]');
      const ratio = rememberedRatios.get(card.id);
      if (ratio) [thumb, media].filter((image) => image?.tagName === 'IMG').forEach((image) => {
        image.width = ratio[0]; image.height = ratio[1];
      });
      const rememberRatio = (image) => {
        if (!image.naturalWidth) return;
        image.width = image.naturalWidth; image.height = image.naturalHeight;
        if (media?.tagName === 'IMG' && !media.naturalWidth) {
          media.width = image.naturalWidth; media.height = image.naturalHeight;
        }
        scheduleLayout();
      };
      if (thumb) {
        thumb.addEventListener('load', () => rememberRatio(thumb));
        if (thumb.complete) rememberRatio(thumb);
      }
      if (media) {
        mediaObserver.observe(media);
        media.addEventListener('load', () => rememberRatio(media));
        media.addEventListener('loadedmetadata', () => {
          if (media.videoWidth) media.style.aspectRatio = `${media.videoWidth} / ${media.videoHeight}`;
        });
        media.addEventListener('error', () => {
          media.hidden = true;
          media.parentElement.querySelector('[role="status"]').hidden = false;
        });
        media.addEventListener('play', () => {
          gallery.querySelectorAll('video').forEach((other) => { if (other !== media) other.pause(); });
        });
      }
    });
    scheduleLayout();
  }
  gallery.addEventListener('click', (event) => {
    const retry = event.target.closest('[data-media-retry]');
    if (!retry) return;
    const media = retry.closest('[data-continuous-media]').querySelector('[data-media-src]');
    media.removeAttribute('src'); loadMedia(media);
  });

  // Keep appended batches across a detail round trip, without storing media or HTML.
  const identity = new URL(global.location.href);
  ['view', 'shape', 'names', 'ui', 'resume_scroll', 'return_to'].forEach((key) => identity.searchParams.delete(key));
  const storageKey = `flowinone-gallery-batches:${identity.pathname}${identity.search}`;
  let savedPosition;
  try {
    savedPosition = JSON.parse(global.sessionStorage.getItem(storageKey) || 'null');
    for (const [id, ratio] of savedPosition?.ratios || []) rememberedRatios.set(id, ratio);
  } catch (_) { /* optional */ }
  const savePosition = () => {
    if (restoring) return;
    const anchor = [...gallery.children].find((card) => card.getBoundingClientRect().bottom > 0 && !card.hidden);
    try {
      const ratios = [...gallery.children].map((card) => {
        const image = card.querySelector('.gallery-card-media img');
        return [card.id, image ? [image.naturalWidth || image.width, image.naturalHeight || image.height] : null];
      }).filter(([, ratio]) => ratio?.[0] && ratio?.[1]);
      global.sessionStorage.setItem(storageKey, JSON.stringify({pages: loadedPages, ratios, scroll: global.scrollY,
        anchor: anchor?.id, offset: anchor?.getBoundingClientRect().top || 0, view: gallery.dataset.galleryView}));
    } catch (_) { /* Browsing also works without storage. */ }
  };

  async function loadNext() {
    if (loading || !next || next.hidden) return false;
    loading = true;
    failed = false;
    next.setAttribute('aria-disabled', 'true');
    gallery.setAttribute('aria-busy', 'true');
    status.textContent = '載入中…';
    try {
      const url = new URL(next.href, global.location.href);
      if (url.origin !== global.location.origin || loadedPages.includes(url.href)) throw new Error('invalid next batch');
      const response = await global.fetch(url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('batch unavailable');
      const page = new global.DOMParser().parseFromString(await response.text(), 'text/html');
      const batch = page.querySelector('[data-gallery-view]');
      if (!batch) throw new Error('batch missing');
      const ids = new Set([...gallery.children].map((card) => card.id));
      const cards = [...batch.children].filter((card) => card.matches('.gallery-card') && !ids.has(card.id));
      gallery.append(...cards);
      loadedPages.push(url.href);
      const following = page.querySelector('[data-browse-next]');
      next.hidden = !following;
      if (following) next.href = following.href;
      status.textContent = following ? '' : '已全部載入';
      bindCards();
      root.dispatchEvent(new global.CustomEvent('flowinone:items-added'));
      setNames(gallery.dataset.galleryNames);
      const search = root.querySelector('[data-browse-filter]');
      if (search?.value) search.dispatchEvent(new global.Event('input'));
      if (continuous()) observeMedia();
      return true;
    } catch (_) {
      failed = true;
      status.textContent = '載入失敗，按「載入下一批」重試。';
      return false;
    } finally {
      loading = false;
      next.removeAttribute('aria-disabled');
      gallery.removeAttribute('aria-busy');
      if (!restoring) maybeLoad();
    }
  }
  function maybeLoad() {
    if (!restoring && !failed && continuous() && next && !next.hidden && next.getBoundingClientRect().top < global.innerHeight + 800) void loadNext();
  }
  const nextObserver = new global.IntersectionObserver(maybeLoad, {rootMargin: '800px 0px'});
  if (next) {
    nextObserver.observe(next);
    next.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); void loadNext();
    });
  }
  function observeMedia() {
    gallery.querySelectorAll('[data-media-src]').forEach((media) => {
      mediaObserver.unobserve(media); mediaObserver.observe(media);
      if (!continuous() && media.tagName === 'VIDEO') media.pause();
    });
  }
  root.addEventListener('flowinone:view-changed', () => { scheduleLayout(); observeMedia(); maybeLoad(); updateShareLink(); });
  root.addEventListener('flowinone:shape-changed', () => { scheduleLayout(); updateShareLink(); });
  global.addEventListener('scroll', maybeLoad, {passive: true});
  global.addEventListener('pagehide', savePosition);
  root.addEventListener('click', (event) => { if (event.target.closest('.gallery-card a')) savePosition(); }, true);
  names?.addEventListener('change', () => setNames(names.value));
  let initial = new URL(global.location.href).searchParams.get('names');
  try { initial ||= global.localStorage.getItem('flowinone-gallery-names'); } catch (_) { /* optional */ }
  setNames(initial || 'auto');
  bindCards(); observeMedia();

  async function restore() {
    const saved = savedPosition;
    const url = new URL(global.location.href);
    if (saved?.scroll > 0 && saved.view === gallery.dataset.galleryView && url.searchParams.get('resume_scroll') !== '0'
        && root.querySelector('[data-resume-scroll]')?.dataset.resumeScroll !== '0') {
      restoring = true;
      // Only replay the existing next chain; stored URLs cannot introduce new destinations.
      for (let index = 0; index < (saved.pages?.length || 0); index += 1) {
        if (!await loadNext()) break;
      }
      await new Promise((resolve) => global.requestAnimationFrame(resolve));
      layout();
      await new Promise((resolve) => global.requestAnimationFrame(resolve));
      const anchor = root.getElementById(saved.anchor);
      global.scrollTo({top: anchor ? global.scrollY + anchor.getBoundingClientRect().top - saved.offset : saved.scroll, behavior: 'instant'});
      restoring = false;
    }
    maybeLoad();
  }
  if (root.readyState === 'loading') root.addEventListener('DOMContentLoaded', restore, {once: true});
  else void restore();
}(window));
