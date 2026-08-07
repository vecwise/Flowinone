(function initializeCatalogSimilarity(global) {
  'use strict';

  const JOB_STATES = {
    pending: '等待背景 worker 分析',
    running: '正在分析本機圖片',
    retry: '分析將稍後重試',
    complete: '分析完成',
    failed: '分析失敗',
  };

  function createCatalogSimilarity(root = global.document) {
    const panel = root.querySelector('[data-catalog-similarity-status]');
    const rebuild = root.getElementById('catalog-similarity-rebuild');
    const dialog = root.querySelector('[data-catalog-similar-dialog]');
    if (!panel || !rebuild || !dialog) return null;
    const statusSummary = panel.querySelector('[data-catalog-similarity-summary]');
    const statusDetail = panel.querySelector('[data-catalog-similarity-detail]');
    const summary = dialog.querySelector('[data-catalog-similar-summary]');
    const results = dialog.querySelector('[data-catalog-similar-results]');
    const empty = dialog.querySelector('[data-catalog-similar-empty]');
    let pollTimer = null;

    const renderStatus = (status) => {
      const groups = Number(status?.duplicate_groups || 0);
      statusSummary.textContent = `已分析 ${Number(status?.analyzed_items || 0)} 張本機圖片${groups ? ` · ${groups} 組完全相同檔案` : ''}`;
      const job = status?.job;
      statusDetail.textContent = job
        ? (job.error_message || JOB_STATES[job.status] || '等待下一次分析')
        : '使用本機 SHA-256 找完全相同檔案，再用 dHash 找視覺相近圖片；只讀取本機圖片。';
      rebuild.disabled = job?.status === 'pending' || job?.status === 'running';
      rebuild.textContent = rebuild.disabled ? '分析中…' : '分析本機圖片';
      if (rebuild.disabled) {
        global.clearTimeout(pollTimer);
        pollTimer = global.setTimeout(() => refreshStatus().catch(() => {}), 1800);
      }
    };

    const refreshStatus = async () => {
      const response = await global.fetch('/api/catalog/similarity/status');
      if (!response.ok) throw new Error('無法讀取圖片分析狀態');
      const payload = await response.json();
      renderStatus(payload);
      return payload;
    };
    const show = () => {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    };
    const escape = (value) => String(value || '').replace(/[&<>"']/g, (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'}[character]));
    const renderMatches = (payload) => {
      results.textContent = '';
      empty.hidden = true;
      if (!payload.analyzed) {
        summary.textContent = '這張圖片尚未分析。先執行「分析本機圖片」後再查看結果。';
        empty.textContent = '分析只讀取本機圖片，並不會修改原始檔或來源標籤。';
        empty.hidden = false;
        return;
      }
      if (!payload.items.length) {
        summary.textContent = '沒有找到符合目前相似度門檻的圖片。';
        empty.textContent = '完全相同會優先顯示；視覺相似使用 64-bit dHash，比對門檻為 8 個位元差異。';
        empty.hidden = false;
        return;
      }
      const duplicateCount = payload.items.filter((item) => item.match_type === 'duplicate').length;
      summary.textContent = duplicateCount
        ? `找到 ${duplicateCount} 張完全相同檔案，以及 ${payload.items.length - duplicateCount} 張視覺相似圖片。`
        : `找到 ${payload.items.length} 張視覺相似圖片。`;
      results.innerHTML = payload.items.map((item) => {
        const kind = item.match_type === 'duplicate' ? '完全相同檔案' : `視覺相似 ${item.similarity_percent}%`;
        const href = item.launch_uri || '#';
        const target = item.target_blank ? ' target="_blank" rel="noopener noreferrer"' : '';
        return `<a class="navigator-similar-card" href="${escape(href)}"${target}>
          <img src="${escape(item.thumbnail_ref || '/static/default_thumbnail.svg')}" alt="" loading="lazy">
          <span><strong>${escape(item.title)}</strong><small>${kind} · ${escape(item.launch_source_label || '')}</small></span>
        </a>`;
      }).join('');
    };

    rebuild.addEventListener('click', async () => {
      rebuild.disabled = true;
      statusDetail.textContent = '正在排程本機圖片分析…';
      try {
        const response = await global.fetch('/api/catalog/similarity/rebuild', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({force: false}),
        });
        if (!response.ok) throw new Error('無法排程圖片分析');
        await refreshStatus();
      } catch (error) {
        statusDetail.textContent = error.message;
        rebuild.disabled = false;
      }
    });
    root.querySelector('.navigator-maintenance')?.addEventListener('toggle', (event) => {
      if (event.currentTarget.open) refreshStatus().catch(() => {});
    });
    root.querySelectorAll('[data-catalog-similar-item]').forEach((button) => {
      button.addEventListener('click', async () => {
        show();
        summary.textContent = '正在查詢…';
        results.textContent = '';
        empty.hidden = true;
        try {
          const response = await global.fetch(`/api/catalog/items/${encodeURIComponent(button.dataset.catalogSimilarItem)}/similar-images`);
          if (!response.ok) throw new Error('無法查詢相似圖片');
          renderMatches(await response.json());
        } catch (error) {
          summary.textContent = error.message;
        }
      });
    });
    return {refreshStatus};
  }

  global.FlowinoneCatalogSimilarity = {createCatalogSimilarity};
  if (global.document) global.FlowinoneCatalogSimilarity.controller = createCatalogSimilarity();
}(window));
