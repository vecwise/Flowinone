(function initializeNavigatorCatalogSync(global) {
  'use strict';

  const ACTIVE_STATUSES = new Set(['syncing', 'retrying']);

  function createCatalogSyncController(options = {}) {
    const root = options.root || global.document;
    const request = options.request || global.fetch.bind(global);
    const timers = options.timers || global;
    const location = options.location || global.location;
    const reindex = root.getElementById('catalog-reindex');
    const incrementalSync = root.getElementById('catalog-incremental-sync');
    const syncRows = new Map(
      [...root.querySelectorAll('[data-catalog-sync-source]')]
        .map((row) => [row.dataset.catalogSyncSource, row]),
    );
    let syncRunning = false;
    let retrySource = null;

    const retryButton = (source) => syncRows.get(source)?.querySelector('[data-catalog-sync-retry]');
    const updateActions = () => {
      syncRows.forEach((row, source) => {
        const button = retryButton(source);
        if (!button) return;
        const isActiveRetry = syncRunning && retrySource === source;
        button.hidden = row.dataset.status !== 'failed' && !isActiveRetry;
        button.disabled = syncRunning;
        button.textContent = isActiveRetry ? '重試中…' : '重試';
      });
      if (reindex) reindex.disabled = syncRunning;
      if (incrementalSync) incrementalSync.disabled = syncRunning;
    };

    const renderSyncState = (source, state = {}) => {
      const row = syncRows.get(source);
      if (!row) return;
      const status = state.status || 'idle';
      const label = row.querySelector('[data-catalog-sync-state]');
      const detail = row.querySelector('[data-catalog-sync-detail]');
      row.dataset.status = status;
      if (status === 'complete') {
        label.textContent = '成功';
        const count = state.count ?? state.item_count ?? 0;
        const changes = state.changed == null ? '' : ` · 更新 ${state.changed} · 略過 ${state.skipped || 0}`;
        detail.textContent = `${count} 筆${changes}${state.retry_count ? ` · 重試 ${state.retry_count} 次` : ''}`;
      } else if (status === 'retrying') {
        label.textContent = '重試中';
        const progress = state.total == null ? '' : `${state.processed || 0}/${state.total} 筆 · `;
        detail.textContent = `${progress}SQLite 暫時忙碌；準備第 ${state.next_attempt || 2}/${state.max_attempts || 4} 次嘗試`;
      } else if (status === 'failed') {
        label.textContent = '最終失敗';
        const progress = state.total == null ? '' : `${state.processed || 0}/${state.total} 筆 · `;
        detail.textContent = `${progress}${state.error || '同步失敗'}`;
      } else if (status === 'syncing') {
        label.textContent = '同步中';
        const progress = state.total == null ? '' : `${state.processed || 0}/${state.total} 筆 · `;
        detail.textContent = `${progress}第 ${state.attempt || 1}/${state.max_attempts || 4} 次嘗試`;
      } else {
        label.textContent = '尚未同步';
        detail.textContent = '—';
      }
      updateActions();
    };

    const renderSelectedStates = (states, sources, activeOnly = false) => {
      sources.forEach((source) => {
        const state = states?.[source];
        if (state && (!activeOnly || ACTIVE_STATUSES.has(state.status))) {
          renderSyncState(source, state);
        }
      });
    };

    const runSync = async (sources, {
      singleSource = null,
      reloadOnSuccess = false,
      fullRescan = false,
    } = {}) => {
      if (syncRunning || !sources.length) return null;
      syncRunning = true;
      retrySource = singleSource;
      sources.forEach((source) => renderSyncState(source, {
        status: 'syncing',
        attempt: 1,
        max_attempts: 4,
      }));
      updateActions();

      let pollInFlight = false;
      const poll = async () => {
        if (pollInFlight) return;
        pollInFlight = true;
        try {
          const response = await request('/api/catalog/sync/status');
          if (response.ok) {
            renderSelectedStates((await response.json()).sources, sources, true);
          }
        } catch (_error) {
          // The POST result remains authoritative if a progress poll is missed.
        } finally {
          pollInFlight = false;
        }
      };
      const pollTimer = timers.setInterval(poll, 100);

      try {
        const response = await request('/api/catalog/sync', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({sources, ...(fullRescan ? {full_rescan: true} : {})}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '同步失敗');
        renderSelectedStates(result, sources);
        const failed = sources.some((source) => result[source]?.status === 'failed');
        if (reloadOnSuccess && !failed) {
          timers.setTimeout(() => location.reload(), 800);
        }
        return result;
      } catch (error) {
        sources.forEach((source) => renderSyncState(source, {
          status: 'failed',
          error: error.message,
        }));
        return null;
      } finally {
        timers.clearInterval(pollTimer);
        syncRunning = false;
        retrySource = null;
        updateActions();
      }
    };

    if (reindex) {
      reindex.addEventListener('click', () => runSync(
        [...syncRows.keys()],
        {reloadOnSuccess: true, fullRescan: true},
      ));
    }
    if (incrementalSync) {
      incrementalSync.addEventListener('click', () => runSync(
        [...syncRows.keys()],
        {reloadOnSuccess: true},
      ));
    }
    syncRows.forEach((row, source) => {
      retryButton(source)?.addEventListener('click', () => runSync(
        [source],
        {singleSource: source},
      ));
    });
    updateActions();

    return {renderSyncState, runSync};
  }

  global.FlowinoneCatalogSync = {createCatalogSyncController};
  if (global.document) {
    global.FlowinoneCatalogSync.controller = createCatalogSyncController();
  }
}(window));
