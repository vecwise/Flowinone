(() => {
  document.querySelectorAll('.current-context, .catalog-share-context').forEach((section) => {
    const button = section.querySelector('[data-copy-context]');
    const input = section.querySelector('[data-context-share-url]');
    const status = section.querySelector('[data-context-copy-status]');
    button?.addEventListener('click', async () => {
      try {
        if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
        await navigator.clipboard.writeText(input.value);
        status.textContent = '已複製條件連結';
      } catch (_) {
        input.focus();
        input.select();
        status.textContent = '連結已選取，請按 ⌘C 或 Ctrl+C 複製';
      }
    });
  });
})();
