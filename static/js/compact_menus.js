(() => {
  'use strict';
  const menus = [...document.querySelectorAll('details.compact-menu, .catalog-toolbar > details.navigator-advanced')];
  menus.forEach((menu) => menu.addEventListener('toggle', () => {
    if (menu.open) menus.forEach((other) => { if (other !== menu && !other.contains(menu)) other.open = false; });
  }));
  document.addEventListener('click', (event) => {
    menus.forEach((menu) => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
    const menu = menus.find((candidate) => candidate.open);
    if (!menu) return;
    menu.open = false;
    menu.querySelector('summary')?.focus();
    event.preventDefault();
  });
})();
