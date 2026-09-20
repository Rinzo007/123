
(() => {
  const rows = [...document.querySelectorAll('#changes .chg')];
  const more = document.querySelector('#changes .chg-more');
  const PAGE = 3;
  let shown = PAGE;
  const paint = () => {
    rows.forEach((r, i) => { r.hidden = i >= shown; });
    const left = rows.length - shown;
    more.hidden = left <= 0;
    more.querySelector('span').textContent = left > 0 ? `${left} more` : '';
  };
  more.addEventListener('click', () => { shown += PAGE; paint(); });
  paint();
})();
