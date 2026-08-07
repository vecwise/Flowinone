(function initializeCatalogSourceWatch(global) {
  'use strict';

  const LABELS = {
    idle: ['尚未觀測', '—'],
    watching: ['正在監看', '來源指紋未變更'],
    changed: ['等待變更穩定', '等待去抖動後排程'],
    queued: ['已排程', '等待背景 worker 同步'],
    running: ['同步中', '背景 worker 正在同步'],
    retry: ['待重試', '同步稍後會自動重試'],
    complete: ['已同步', '最新變更已完成同步'],
    unavailable: ['無法觀測', '來源目前不可用'],
    error: ['發生錯誤', '下一個週期會再試一次'],
  };

  function createCatalogSourceWatch(root = global.document) {
    const panel = root.querySelector('[data-catalog-watch-status]');
    const toggle = root.getElementById('catalog-source-watch-toggle');
    if (!panel || !toggle) return null;
    const summary = panel.querySelector('[data-catalog-watch-summary]');
    const rows = new Map([...panel.querySelectorAll('[data-catalog-watch-source]')]
      .map((row) => [row.dataset.catalogWatchSource, row]));

    const render = (status) => {
      const enabled = Boolean(status?.enabled);
      panel.dataset.enabled = String(enabled);
      toggle.setAttribute('aria-pressed', String(enabled));
      toggle.textContent = enabled ? '暫停偵測' : '啟用自動偵測';
      summary.textContent = enabled
        ? `每 ${status.poll_interval_seconds} 秒檢查一次，變更會在 ${status.debounce_seconds} 秒後排程`
        : '目前關閉；不會讀取來源';
      rows.forEach((row, source) => {
        const sourceState = status?.sources?.[source] || {};
        const state = sourceState.state || 'idle';
        const [label, detail] = LABELS[state] || LABELS.idle;
        row.dataset.watchState = state;
        row.querySelector('[data-catalog-watch-state]').textContent = label;
        row.querySelector('[data-catalog-watch-detail]').textContent = sourceState.error
          || (sourceState.observed_at ? `最後檢查：${sourceState.observed_at}` : detail);
      });
    };
    const refresh = async () => {
      const response = await global.fetch('/api/catalog/watch');
      if (!response.ok) throw new Error('無法讀取自動偵測狀態');
      const status = await response.json();
      render(status);
      return status;
    };

    toggle.addEventListener('click', async () => {
      const enabled = panel.dataset.enabled !== 'true';
      toggle.disabled = true;
      try {
        const response = await global.fetch('/api/catalog/watch', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({enabled}),
        });
        if (!response.ok) throw new Error('無法更新自動偵測設定');
        render(await response.json());
      } catch (error) {
        summary.textContent = error.message;
      } finally {
        toggle.disabled = false;
      }
    });
    root.querySelector('.navigator-maintenance')?.addEventListener('toggle', (event) => {
      if (event.currentTarget.open) refresh().catch(() => {});
    });

    return {refresh, render};
  }

  global.FlowinoneCatalogSourceWatch = {createCatalogSourceWatch};
  if (global.document) {
    global.FlowinoneCatalogSourceWatch.controller = createCatalogSourceWatch();
  }
}(window));
