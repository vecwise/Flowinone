(function initializeDuplicateReview(global) {
  'use strict';

  const apiError = async (response, fallback) => {
    const payload = await response.json().catch(() => ({}));
    return payload.error || fallback;
  };

  const copyText = async (value) => {
    if (global.navigator.clipboard?.writeText) {
      await global.navigator.clipboard.writeText(value);
      return;
    }
    const field = global.document.createElement('textarea');
    field.value = value;
    field.setAttribute('readonly', '');
    field.style.position = 'fixed';
    field.style.opacity = '0';
    global.document.body.append(field);
    field.select();
    const copied = global.document.execCommand('copy');
    field.remove();
    if (!copied) throw new Error('瀏覽器不允許複製路徑');
  };

  function createDuplicateReview(root = global.document) {
    const page = root.querySelector('[data-duplicate-review]');
    const status = root.querySelector('[data-duplicate-review-status]');
    if (!page || !status) return null;

    const announce = (message) => { status.textContent = message; };

    page.addEventListener('click', async (event) => {
      const canonical = event.target.closest('[data-duplicate-canonical]');
      if (canonical) {
        canonical.disabled = true;
        announce('正在儲存參考圖片選擇…');
        try {
          const response = await global.fetch('/api/catalog/duplicate-review/canonical', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              content_hash: canonical.dataset.contentHash,
              canonical_item_id: canonical.dataset.duplicateCanonical,
            }),
          });
          if (!response.ok) throw new Error(await apiError(response, '無法儲存參考圖片'));
          announce('已儲存參考圖片選擇。');
          global.location.reload();
        } catch (error) {
          canonical.disabled = false;
          announce(error.message);
        }
        return;
      }

      const reveal = event.target.closest('[data-duplicate-reveal]');
      if (reveal) {
        reveal.disabled = true;
        announce('正在於檔案管理器顯示圖片…');
        try {
          const response = await global.fetch(
            `/api/catalog/duplicate-review/items/${encodeURIComponent(reveal.dataset.duplicateReveal)}/reveal`,
            {method: 'POST'},
          );
          if (!response.ok) throw new Error(await apiError(response, '無法顯示本機圖片'));
          announce('已在檔案管理器顯示圖片。');
        } catch (error) {
          announce(error.message);
        } finally {
          reveal.disabled = false;
        }
        return;
      }

      const copy = event.target.closest('[data-duplicate-copy-path]');
      if (copy) {
        try {
          await copyText(copy.dataset.duplicateCopyPath || '');
          announce('已複製本機檔案路徑。');
        } catch (error) {
          announce(error.message);
        }
      }
    });
    return {announce};
  }

  global.FlowinoneDuplicateReview = {createDuplicateReview};
  if (global.document) global.FlowinoneDuplicateReview.controller = createDuplicateReview();
}(window));
