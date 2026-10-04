document.addEventListener('DOMContentLoaded', () => {
  const grid = document.querySelector('[data-gallery-view]');
  const search = document.querySelector('[data-browse-filter]');
  const sort = document.querySelector('[data-browse-sort]');
  if (!grid) return;
  search?.addEventListener('input', () => {
    const term = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    [...grid.children].forEach((card) => {
      card.hidden = !card.textContent.toLocaleLowerCase().includes(term);
      if (!card.hidden) visible += 1;
    });
    const empty = document.querySelector('[data-browse-empty]');
    if (empty) empty.hidden = visible > 0 || !grid.children.length;
  });
  sort?.addEventListener('click', () => {
    const direction = sort.dataset.direction === 'asc' ? 'asc' : 'desc';
    [...grid.children].sort((a, b) => direction === 'asc'
      ? a.dataset.name.localeCompare(b.dataset.name) : b.dataset.name.localeCompare(a.dataset.name))
      .forEach((card) => grid.appendChild(card));
    sort.dataset.direction = direction === 'asc' ? 'desc' : 'asc';
    sort.textContent = direction === 'asc' ? '名稱 A → Z' : '名稱 Z → A';
  });
});
