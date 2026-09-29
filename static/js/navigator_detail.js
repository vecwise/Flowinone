(function initializeNavigatorDetail(global) {
  'use strict';

  function createNavigatorDetail(root = global.document) {
    const dialog = root?.querySelector('[data-gallery-detail]');
    const page = root?.querySelector('[data-navigator-query-payload]');
    if (!dialog || !page) return null;

    const query = JSON.parse(page.dataset.navigatorQueryPayload || '{}');
    const params = new URLSearchParams();
    (query.sources || []).forEach((source) => params.append('source', source));
    if (query.folder) params.set('folder', query.folder);
    const sourceQuery = params.toString();
    const suffix = sourceQuery ? `?${sourceQuery}` : '';
    const status = dialog.querySelector('[data-gallery-detail-status]');
    const content = dialog.querySelector('[data-gallery-detail-content]');
    const image = dialog.querySelector('[data-gallery-detail-image]');
    const video = dialog.querySelector('[data-gallery-detail-video]');
    const source = dialog.querySelector('[data-gallery-detail-source]');
    const folder = dialog.querySelector('[data-gallery-detail-folder]');
    const title = dialog.querySelector('[data-gallery-detail-title]');
    const description = dialog.querySelector('[data-gallery-detail-description]');
    const tags = dialog.querySelector('[data-gallery-detail-tags]');
    const launch = dialog.querySelector('[data-gallery-detail-launch]');
    const related = dialog.querySelector('[data-gallery-detail-related]');
    const back = dialog.querySelector('[data-gallery-detail-back]');
    const close = dialog.querySelector('[data-gallery-detail-close]');
    let activeId = null;
    let sequence = 0;
    let returnFocus = null;
    const trail = [];

    const filterUrl = (key, value) => {
      const url = new URL(global.location.href);
      url.searchParams.delete('cursor');
      if (key === 'tags') {
        url.searchParams.delete('folder');
        url.searchParams.set('tags', value);
      } else {
        url.searchParams.set('folder', value);
        url.searchParams.delete('source');
        url.searchParams.append('source', 'bookmarks');
      }
      return `${url.pathname}${url.search}`;
    };

    const requestJson = async (url) => {
      const response = await global.fetch(url);
      if (!response.ok) throw new Error('無法載入素材詳情');
      return response.json();
    };
    const postView = (id) => {
      void global.fetch(`/api/catalog/items/${encodeURIComponent(id)}/events`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event_type: 'view'}),
      }).catch(() => {});
    };

    const renderRelated = (items) => {
      related.replaceChildren();
      if (!items.length) {
        const empty = root.createElement('p');
        empty.textContent = '目前沒有相關內容。';
        related.append(empty);
        return;
      }
      items.forEach((item) => {
        const button = root.createElement('button');
        button.type = 'button';
        button.className = 'navigator-detail-related-item';
        const thumb = root.createElement('img');
        thumb.src = item.thumbnail_ref || '/static/default_thumbnail.svg';
        thumb.alt = '';
        thumb.loading = 'lazy';
        const label = root.createElement('span');
        const name = root.createElement('strong');
        name.textContent = item.title;
        const hint = root.createElement('small');
        const shared = item.reason?.shared_tags || [];
        hint.textContent = shared.length ? `共同標籤：${shared.slice(0, 2).join('、')}` : (item.launch_source_label || '相關內容');
        label.append(name, hint);
        button.append(thumb, label);
        button.addEventListener('click', () => {
          trail.push(activeId);
          void load(item.id, true);
        });
        related.append(button);
      });
    };

    const renderItem = (item) => {
      const thumbnail = item.thumbnail_ref || '/static/default_thumbnail.svg';
      image.src = thumbnail;
      image.alt = item.title;
      const playable = item.item_type === 'video' && Boolean(item.playback_uri);
      image.hidden = playable;
      video.hidden = !playable;
      if (playable) {
        video.poster = thumbnail;
        video.src = item.playback_uri;
      }
      source.textContent = [item.launch_source_label, item.item_type].filter(Boolean).join(' · ');
      folder.hidden = item.launch_source !== 'bookmarks' || !item.folder_path;
      if (!folder.hidden) {
        folder.textContent = `資料夾：${item.folder_path}`;
        folder.href = filterUrl('folder', item.folder_path);
      }
      title.textContent = item.title;
      description.textContent = item.description || '這個項目目前沒有描述。';
      tags.replaceChildren();
      (item.tags || []).slice(0, 12).forEach((tag) => {
        const chip = root.createElement('a');
        chip.textContent = tag;
        chip.href = filterUrl('tags', tag);
        tags.append(chip);
      });
      launch.hidden = !item.launch_uri;
      if (item.launch_uri) launch.href = item.launch_uri;
      if (item.target_blank) {
        launch.target = '_blank';
        launch.rel = 'noopener noreferrer';
      } else {
        launch.removeAttribute('target');
        launch.removeAttribute('rel');
      }
      back.hidden = trail.length === 0;
      content.hidden = false;
    };

    async function load(id, recordView = false) {
      const current = ++sequence;
      activeId = id;
      video.pause?.();
      video.removeAttribute('src');
      video.hidden = true;
      status.textContent = '正在載入素材…';
      content.hidden = true;
      back.hidden = trail.length === 0;
      try {
        const item = await requestJson(`/api/catalog/items/${encodeURIComponent(id)}${suffix}`);
        if (current !== sequence) return;
        renderItem(item);
        status.textContent = '';
        related.textContent = '正在尋找相關內容…';
        if (recordView) postView(id);
        try {
          const result = await requestJson(`/api/catalog/items/${encodeURIComponent(id)}/related${suffix}`);
          if (current === sequence) renderRelated(result.items || []);
        } catch (_) {
          if (current === sequence) related.textContent = '暫時無法載入相關內容。';
        }
      } catch (_) {
        if (current === sequence) status.textContent = '無法載入詳情，請關閉後直接開啟來源。';
      }
    }

    root.querySelectorAll('[data-gallery-detail-open]').forEach((link) => {
      link.addEventListener('click', (event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const item = link.closest('[data-navigator-item]');
        if (!item) return;
        event.preventDefault();
        returnFocus = link;
        trail.length = 0;
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        close.focus();
        void load(item.dataset.navigatorItem);
      });
    });
    back.addEventListener('click', () => {
      const previous = trail.pop();
      if (previous) void load(previous);
    });
    close.addEventListener('click', () => {
      if (typeof dialog.close === 'function') dialog.close();
      else {
        dialog.removeAttribute('open');
        returnFocus?.focus();
      }
    });
    dialog.addEventListener('close', () => {
      sequence += 1;
      activeId = null;
      video.pause?.();
      video.removeAttribute('src');
      returnFocus?.focus();
    });
    launch.addEventListener('click', () => {
      if (!activeId) return;
      void global.fetch(`/api/catalog/items/${encodeURIComponent(activeId)}/events`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event_type: 'open'}),
      }).catch(() => {});
    });
    return {load};
  }

  global.FlowinoneNavigatorDetail = {createNavigatorDetail};
  if (global.document) global.FlowinoneNavigatorDetail.controller = createNavigatorDetail();
}(window));
