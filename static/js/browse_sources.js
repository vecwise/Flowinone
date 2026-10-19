(() => {
  'use strict';
  const form = document.querySelector('[data-global-sources]');
  if (!form) return;
  const inputs = [...form.querySelectorAll('input[name="source"]')];
  const hint = document.getElementById('global-source-hint');
  let timer;
  let submitting = false;
  for (const input of inputs) {
    input.addEventListener('change', () => {
      if (!inputs.some((item) => item.checked)) {
        input.checked = true;
        hint.textContent = '至少保留一個瀏覽來源';
        return;
      }
      hint.textContent = '正在更新瀏覽來源…';
      clearTimeout(timer);
      timer = setTimeout(() => {
        try { sessionStorage.setItem('flowinone-source-focus', input.value); } catch (_) { /* Storage is optional. */ }
        form.requestSubmit();
      }, 180);
    });
  }
  form.addEventListener('submit', (event) => {
    clearTimeout(timer);
    if (submitting) { event.preventDefault(); return; }
    submitting = true;
    // Presentation controls can change the URL without reloading this form.
    form.querySelector('input[name="return_to"]').value = window.location.pathname + window.location.search + window.location.hash;
    form.setAttribute('aria-busy', 'true');
    hint.textContent = '正在更新瀏覽來源…';
  });
  try {
    const focused = sessionStorage.getItem('flowinone-source-focus');
    sessionStorage.removeItem('flowinone-source-focus');
    inputs.find((input) => input.value === focused)?.focus({preventScroll: true});
  } catch (_) { /* Native controls still work without storage. */ }
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) window.location.reload();
  });
})();
