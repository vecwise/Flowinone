(() => {
  const page = document.querySelector('.gallery-lab');
  if (!page) return;
  page.dataset.scriptReady = '1';
  page.querySelector('.lab-switcher-options .is-current')?.scrollIntoView({block: 'nearest', inline: 'center'});

  const search = page.querySelector('#lab-search');
  let searchTimer;
  search?.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => search.form.requestSubmit(), 420);
  });
  page.querySelectorAll('.lab-filter input[type="checkbox"], .lab-filter select').forEach((control) => {
    control.addEventListener('change', () => control.form.requestSubmit());
  });

  page.querySelectorAll('.lab-favorite').forEach((button) => {
    button.addEventListener('click', async () => {
      const card = button.closest('[data-gallery-item]');
      const enabled = button.dataset.favorite === '1';
      button.disabled = true;
      try {
        const response = await fetch(`/api/catalog/items/${card.dataset.galleryItem}/events`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({event_type: enabled ? 'unfavorite' : 'favorite'})
        });
        if (!response.ok) return;
        button.dataset.favorite = enabled ? '0' : '1';
        button.setAttribute('aria-pressed', enabled ? 'false' : 'true');
        button.textContent = enabled ? '收藏' : '已收藏';
      } finally {
        button.disabled = false;
      }
    });
  });

  if (page.dataset.design.indexOf('gsap-') !== 0) return;
  const startGsap = () => {
    if (!window.gsap || !window.ScrollTrigger) return;
    page.dataset.gsapStarted = '1';
    const {gsap, ScrollTrigger} = window;
    gsap.registerPlugin(ScrollTrigger);
    const mm = gsap.matchMedia();
    mm.add({
      animate: '(prefers-reduced-motion: no-preference)',
      desktop: '(min-width: 761px)'
    }, (context) => {
      page.dataset.motionPreference = context.conditions.animate ? 'animate' : 'reduce';
      if (!context.conditions.animate) return;
      const hero = gsap.timeline({defaults: {duration: .72, ease: 'power3.out'}});
      hero.from('.lab-hero-copy > *', {y: 34, autoAlpha: 0, stagger: .08})
          .from('.lab-hero-visual', {x: 42, autoAlpha: 0}, '<.12');

      const design = page.dataset.design;
      if (design === 'gsap-filmstrip') {
        gsap.from('.lab-card', {y: 54, rotateX: 5, autoAlpha: 0, duration: .76, stagger: .075, ease: 'power3.out', scrollTrigger: {trigger: '.lab-results', start: 'top 76%', once: true}});
      }
      if (design === 'gsap-masonry') {
        ScrollTrigger.batch('.lab-card', {
          start: 'top 84%',
          once: true,
          batchMax: 5,
          onEnter: (cards) => gsap.from(cards, {y: 54, autoAlpha: 0, duration: .65, stagger: .07, ease: 'power2.out', overwrite: true})
        });
      }
      if (design === 'gsap-rhythm') {
        gsap.from('.lab-card', {scale: .72, rotate: -2, autoAlpha: 0, duration: .64, stagger: {each: .045, from: 'center', grid: 'auto'}, ease: 'back.out(1.35)', scrollTrigger: {trigger: '.lab-grid', start: 'top 82%', once: true}});
      }
      return () => hero.kill();
    }, page);
    window.addEventListener('pagehide', () => mm.revert(), {once: true});
    window.addEventListener('load', () => ScrollTrigger.refresh(), {once: true});
  };
  if (document.readyState === 'complete') startGsap();
  else window.addEventListener('load', startGsap, {once: true});
})();
