document.addEventListener('DOMContentLoaded', () => {
  const thumbnailStatusRegion = document.getElementById('thumbnailSyncStatus');
    const pendingStatuses = new Set(['missing', 'queued', 'fetching']);
    const pendingIds = new Set(
      Array.from(document.querySelectorAll('[data-thumbnail-id][data-thumbnail-status]'))
        .filter(element => pendingStatuses.has(element.dataset.thumbnailStatus))
        .map(element => element.dataset.thumbnailId)
    );

    function updateThumbnailItem(item) {
      const containers = document.querySelectorAll(`[data-thumbnail-id="${item.id}"]`);
      containers.forEach(container => {
        container.dataset.thumbnailStatus = item.status;
        container.dataset.thumbnailProvider = item.provider || 'metadata';
        container.querySelectorAll('[data-thumbnail-image]').forEach(image => {
          if (item.thumbnail_url && image.getAttribute('src') !== item.thumbnail_url) {
            image.setAttribute('src', item.thumbnail_url);
          }
        });
        const state = container.querySelector('[data-thumbnail-state]');
        if (state) {
          if (item.status === 'ready') {
            state.remove();
          } else if (item.status === 'fetching') {
            state.textContent = '更新中';
          } else if (item.status === 'queued') {
            state.textContent = '排隊中';
          } else {
            state.textContent = item.thumbnail_url ? '暫用封面' : '本機封面';
          }
        }
      });
      if (!pendingStatuses.has(item.status)) {
        pendingIds.delete(item.id);
      }
    }

    async function pollPendingThumbnails() {
      if (!pendingIds.size) {
        if (thumbnailStatusRegion) thumbnailStatusRegion.textContent = '書籤縮圖已完成更新。';
        return;
      }
      const ids = Array.from(pendingIds).slice(0, 200);
      try {
        const response = await fetch(`/api/bookmark-thumbnails/status?ids=${encodeURIComponent(ids.join(','))}`, {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(`Thumbnail status ${response.status}`);
        const payload = await response.json();
        (payload.items || []).forEach(updateThumbnailItem);
      } catch (error) {
        if (thumbnailStatusRegion) thumbnailStatusRegion.textContent = '目前無法取得縮圖更新狀態。';
      }
      if (pendingIds.size) window.setTimeout(pollPendingThumbnails, 3000);
    }

    if (pendingIds.size) window.setTimeout(pollPendingThumbnails, 3000);

});
