(function () {
  'use strict';
  const root = document.getElementById('eagle-library-management');
  if (!root) return;
  const status = root.querySelector('[data-eagle-library-status]');
  const preview = root.querySelector('#eagle-cleanup-preview');
  const pin = root.querySelector('#eagle-pin-primary');
  const review = root.querySelector('#eagle-cleanup-review');
  const confirm = root.querySelector('#eagle-cleanup-confirm');
  const cancel = root.querySelector('#eagle-cleanup-cancel');
  let token = null;
  let busy = false;
  const request = async (path, payload) => {
    const response = await fetch(`/api/catalog/eagle/${path}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '操作失敗，請重試。');
    return result;
  };
  const run = async (message, action) => {
    if (busy) return;
    busy = true;
    const buttons = [...root.querySelectorAll('button')];
    const disabled = buttons.map((button) => button.disabled);
    buttons.forEach((button) => { button.disabled = true; });
    status.textContent = message;
    try { await action(); }
    catch (error) {
      token = null;
      review.hidden = true;
      status.textContent = error.message;
    } finally {
      buttons.forEach((button, index) => { button.disabled = disabled[index]; });
      confirm.disabled = !token;
      busy = false;
    }
  };
  pin?.addEventListener('click', () => run('正在確認主要庫…', async () => {
    await request('primary', {identity: pin.dataset.identity});
    window.location.reload();
  }));
  preview.addEventListener('click', () => run('正在完整比對主要庫，請稍候…', async () => {
    token = null;
    review.hidden = true;
    const result = await request('cleanup/preview', {});
    status.textContent = `已比對主要庫「${result.library.name || result.library.identity}」共 ${result.live_count} 筆。`;
    if (!result.count) {
      status.textContent += ' 沒有需要清理的 Eagle 紀錄。';
      return;
    }
    token = result.token;
    root.querySelector('#eagle-cleanup-summary').textContent = `將永久清理 ${result.count} 筆不在主要庫內的 Eagle 來源紀錄。以下最多顯示 10 筆：`;
    const sample = root.querySelector('#eagle-cleanup-sample');
    sample.replaceChildren();
    result.sample.forEach((item) => {
      const li = document.createElement('li');
      li.textContent = `${item.title} (${item.id})`;
      sample.append(li);
    });
    review.hidden = false;
  }));
  cancel.addEventListener('click', () => {
    token = null;
    review.hidden = true;
    status.textContent = '已取消清理，DB 未變更。';
  });
  confirm.addEventListener('click', () => {
    if (!token) return;
    run('正在重新核對主要庫、備份並清理…', async () => {
      const result = await request('cleanup', {token});
      token = null;
      review.hidden = true;
      status.textContent = `已清理 ${result.deleted_origins} 筆 Eagle 來源紀錄、${result.deleted_items} 筆獨立項目。` +
        (result.backup_path ? ` 備份：${result.backup_path}` : '');
    });
  });
})();
