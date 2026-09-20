
(() => {
  const nav = document.querySelector('nav'), b = nav && nav.querySelector('.navburger');
  if (!b) return;
  const set = (on) => { nav.classList.toggle('open', on); b.setAttribute('aria-expanded', String(on)); };
  b.addEventListener('click', (e) => { e.stopPropagation(); set(!nav.classList.contains('open')); });
  nav.querySelectorAll('.lnk').forEach(a => a.addEventListener('click', () => set(false)));
  document.addEventListener('click', (e) => { if (!nav.contains(e.target)) set(false); });
  addEventListener('keydown', (e) => { if (e.key === 'Escape') set(false); });
})();
