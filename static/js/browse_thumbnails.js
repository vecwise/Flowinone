document.addEventListener('DOMContentLoaded', () => {
  const tools = document.querySelector('[data-thumbnail-tools]');
  if (!tools) return;
  const cards = Array.from(document.querySelectorAll('[data-thumbnail-id]'));
  const pageIds = [...new Set(cards.map(card => card.dataset.thumbnailId))];
  const scope = tools.querySelector('[data-thumbnail-scope]');
  const batchButton = tools.querySelector('[data-thumbnail-batch]');
  const runRegion = tools.querySelector('[data-thumbnail-run]');
  const statusRegion = document.getElementById('thumbnailSyncStatus');
  const progress = tools.querySelector('[data-thumbnail-progress]');
  const results = tools.querySelector('[data-thumbnail-results]');
  const resultSummary = tools.querySelector('[data-thumbnail-result-summary]');
  const resultList = tools.querySelector('[data-thumbnail-result-list]');
  const busy = new Set(['queued', 'fetching']);
  const autoPending = new Set(cards.filter(card => busy.has(card.dataset.thumbnailStatus)).map(card => card.dataset.thumbnailId));
  const storageKey = 'flowinone.thumbnail-retry.v1';
  let run = null;
  let submitting = false;
  let timer = null;
  let polling = false;
  let pollError = false;

  function active() {
    return run && run.items.some(item => busy.has(item.status));
  }

  function save() {
    try { window.localStorage.setItem(storageKey, JSON.stringify(run)); } catch (_) { /* Private browsing/storage quota. */ }
  }

  function usable(item) {
    return item.thumbnail_url && item.provider && item.provider !== 'site-tile';
  }

  function succeeded(item) {
    return item.status === 'ready' && usable(item);
  }

  function failureReason(item) {
    const error = String(item.error || '').toLowerCase();
    const reasons = [
      [/no explicit thumbnail/, '公開頁面沒有可用封面'],
      [/http (404|410)|removed|unavailable/, '內容已刪除、下架或目前不可用'],
      [/http (403|400|999)|forbidden/, '網站拒絕存取或限制公開瀏覽'],
      [/http 401/, '需要登入或授權'],
      [/http 429|too many requests/, '網站限流，請稍後重試'],
      [/certificate|ssl error/, '網站憑證驗證失敗'],
      [/timeout|timed out/, '網站連線逾時'],
      [/too small/, '網站提供的圖片尺寸不足'],
      [/valid raster|cannot identify|returned html/, '網站沒有回傳可用圖片'],
      [/private or reserved|localhost is blocked/, '此網址不是可爬取的公開網站'],
      [/exceeds/, '圖片或文件超過下載大小上限'],
      [/hostname resolution|name resolution|failed to resolve/, '找不到網站位址'],
    ];
    return reasons.find(([pattern]) => pattern.test(error))?.[1] || '目前無法取得可用縮圖，請稍後重試';
  }

  function updateThumbnailItem(item) {
    cards.filter(card => card.dataset.thumbnailId === item.id).forEach(card => {
      card.dataset.thumbnailStatus = item.status;
      card.dataset.thumbnailProvider = item.provider || '';
      const media = card.querySelector('.gallery-card-media');
      if (usable(item) && media) {
        let image = media.querySelector('[data-thumbnail-image]');
        if (!image) {
          media.querySelectorAll('.gallery-fallback-mark, .gallery-fallback-copy').forEach(node => node.remove());
          media.classList.remove('is-site-fallback');
          image = document.createElement('img');
          image.alt = '';
          image.width = 640;
          image.height = 420;
          image.loading = 'lazy';
          image.decoding = 'async';
          image.dataset.thumbnailImage = '';
          media.prepend(image);
        }
        // The image route stays the same after a refresh; use the file version
        // so a successful retry cannot keep showing a browser-cached old image.
        const version = item.image_version || String(run?.started || '');
        const src = `${item.thumbnail_url}${item.thumbnail_url.includes('?') ? '&' : '?'}v=${encodeURIComponent(version)}`;
        if (image.getAttribute('src') !== src) image.setAttribute('src', src);
      }
      const state = card.querySelector('[data-thumbnail-state]');
      if (state) {
        const labels = {ready: '縮圖已就緒', queued: '排隊中', fetching: '重爬中', retry: '重爬失敗，待重試', failed: '重爬失敗', missing: '尚未爬取', unknown: '找不到縮圖項目'};
        state.textContent = labels[item.status] || item.status;
        if (item.error) state.textContent += `：${failureReason(item)}`;
        state.title = item.error || '';
      }
    });
    if (!busy.has(item.status)) autoPending.delete(item.id);
  }

  function render() {
    const working = submitting || active();
    tools.hidden = !(pageIds.length || tools.dataset.chromePage === 'true' || run);
    tools.setAttribute('aria-busy', String(Boolean(working)));
    batchButton.disabled = Boolean(working || (scope.value === 'page' && !pageIds.length));
    scope.disabled = Boolean(working);
    document.querySelectorAll('[data-thumbnail-retry]').forEach(button => { button.disabled = Boolean(working); });
    if (!run) return;
    runRegion.hidden = false;
    const total = run.items.length;
    const queued = run.items.filter(item => item.status === 'queued').length;
    const fetching = run.items.filter(item => item.status === 'fetching').length;
    const done = total - queued - fetching;
    const success = run.items.filter(succeeded).length;
    const failed = done - success;
    progress.max = Math.max(1, total);
    progress.value = total ? done : 1;
    progress.hidden = !total;
    const message = total
      ? `${working ? '重爬進度' : '本輪完成'} ${done} / ${total}：成功 ${success}、失敗 ${failed}、排隊 ${queued}、處理中 ${fetching}。`
      : '這個範圍沒有需要重試的縮圖。';
    statusRegion.textContent = message + (pollError ? ' 暫時無法取得最新進度，稍後自動重查；工作仍在背景執行。' : '');
    const finished = run.items.filter(item => !busy.has(item.status));
    results.hidden = !finished.length;
    resultSummary.textContent = `查看本輪結果（成功 ${success}、失敗 ${failed}）`;
    const fragment = document.createDocumentFragment();
    finished.sort((a, b) => Number(succeeded(a)) - Number(succeeded(b))).forEach(item => {
      const li = document.createElement('li');
      const label = succeeded(item) ? '成功' : '失敗';
      li.textContent = `${label} · ${item.title || item.id}${!succeeded(item) ? `：${failureReason(item)}` : ''}`;
      li.title = item.error || '';
      fragment.append(li);
    });
    resultList.replaceChildren(fragment);
  }

  function schedule(delay = 3000) {
    if (timer !== null) window.clearTimeout(timer);
    if (active() || autoPending.size) timer = window.setTimeout(poll, delay);
  }

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {headers: {Accept: 'application/json', ...(options.body ? {'Content-Type': 'application/json'} : {})}, ...options});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  async function poll() {
    timer = null;
    if (polling) return;
    polling = true;
    const ids = [...new Set([...(run?.items || []).filter(item => busy.has(item.status)).map(item => item.id), ...autoPending])];
    try {
      for (let offset = 0; offset < ids.length; offset += 200) {
        const payload = await jsonRequest(`/api/bookmark-thumbnails/status?ids=${encodeURIComponent(ids.slice(offset, offset + 200).join(','))}`);
        for (const item of payload.items || []) {
          updateThumbnailItem(item);
          const index = run?.items.findIndex(old => old.id === item.id && busy.has(old.status)) ?? -1;
          if (index >= 0) run.items[index] = item;
        }
      }
      pollError = false;
    } catch (_) {
      pollError = true;
    } finally {
      polling = false;
      if (run) save();
      render();
      schedule(pollError ? 9000 : 3000);
    }
  }

  async function start(url, payload) {
    if (submitting || active()) return;
    submitting = true;
    pollError = false;
    render();
    runRegion.hidden = false;
    progress.hidden = true;
    results.hidden = true;
    statusRegion.textContent = '正在排入縮圖重爬工作…';
    try {
      const response = await jsonRequest(url, {method: 'POST', body: JSON.stringify(payload)});
      run = {started: Date.now(), items: response.items || []};
      run.items.forEach(updateThumbnailItem);
      save();
    } catch (error) {
      // Keep a failed request distinct from a completed background run.
      run = null;
      try { window.localStorage.removeItem(storageKey); } catch (_) { /* Optional persistence. */ }
      statusRegion.textContent = `無法排入重爬工作：${error.message}。請稍後再試。`;
    } finally {
      submitting = false;
      render();
      schedule(500);
    }
  }

  batchButton.addEventListener('click', () => start('/api/bookmark-thumbnails/retry', {
    scope: scope.value,
    ...(scope.value === 'page' ? {ids: pageIds} : {}),
  }));
  scope.addEventListener('change', render);
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-thumbnail-retry]');
    if (!button) return;
    event.preventDefault();
    start('/api/bookmark-thumbnails/enqueue', {ids: [button.dataset.thumbnailRetry], force: true});
  });

  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey));
    if (saved && Number.isFinite(saved.started) && Date.now() - saved.started < 86400000 &&
        Array.isArray(saved.items) && saved.items.length <= 10000 &&
        saved.items.every(item => /^[a-f0-9]{40}$/.test(item.id) && typeof item.status === 'string')) run = saved;
  } catch (_) { /* A fresh run works even when storage is unavailable. */ }
  if (run) run.items.forEach(updateThumbnailItem);
  render();
  schedule(500);
});
