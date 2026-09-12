// Local-first. The feed paints from cache on open, then reconciles with the
// network. The app never shows an empty loading screen after the first visit,
// which matters on Kerala mobile data far more than any animation does.

const FILTERS = [
  ['all', 'All'], ['awarded', 'Winners only'], ['nh', 'NH66'],
  ['bridge', 'Bridges'], ['big', 'Above 10 Cr'],
];

let active = 'all';
const CK = 'ki.feed.v1';

const el = (id) => document.getElementById(id);

function chips() {
  el('chips').innerHTML = FILTERS.map(([k, label]) =>
    `<button class="chip" data-k="${k}" aria-pressed="${k === active}">${label}</button>`
  ).join('');
  el('chips').querySelectorAll('.chip').forEach(c => {
    c.onclick = () => {
      if (c.dataset.k === active) return;
      active = c.dataset.k;
      chips();
      load();
    };
  });
}

function skeleton(n = 4) {
  el('feed').innerHTML = Array.from({ length: n }, () => `
    <div class="sk">
      <div class="bar" style="width:34%;margin-bottom:11px"></div>
      <div class="bar" style="width:92%;margin-bottom:6px"></div>
      <div class="bar" style="width:64%;margin-bottom:13px"></div>
      <div class="bar" style="width:42%;height:17px"></div>
    </div>`).join('');
}

// Relative time is computed per card at render, not stored. A cached feed
// opened the next morning must not claim a winner was "confirmed 4 min ago".
function ago(iso) {
  if (!iso) return 'awaiting confirmation';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'confirmed just now';
  if (mins < 60) return `confirmed ${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `confirmed ${h} hr ago`;
  const d = Math.round(h / 24);
  return d === 1 ? 'confirmed yesterday' : `confirmed ${d} days ago`;
}

function card(r) {
  const awarded = r.status === 'awarded';
  const v = r.display_variance || '';
  const dir = v.startsWith('-') ? 'dn' : 'up';
  return `
  <article class="card">
    <div class="top">
      ${r.road_name ? `<span class="pill p-road">${esc(r.road_name)}</span>` : ''}
      <span class="pill ${awarded ? 'p-awarded' : 'p-opened'}">${awarded ? 'Awarded' : 'Bids opened'}</span>
      <span class="dist">${esc((r.districts || []).join(', '))}</span>
    </div>
    <div class="title">${esc(r.work_title || '')}</div>
    <div class="money">
      <span class="val">${esc(r.display_value || 'Value not published')}</span>
      ${v ? `<span class="var ${dir}">${esc(v)}</span>` : ''}
    </div>
    <div class="win">${esc(r.winner_name || 'Evaluation in progress')}</div>
    <div class="foot">
      <span>${ago(r.awarded_on || r.last_seen)}</span>
      <span class="auth">${esc(r.authority || '')}</span>
    </div>
  </article>`;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function render(items) {
  if (!items.length) {
    el('feed').innerHTML = `<div class="empty"><b>Nothing here yet</b>
      No awards match this filter. Try All, or check back after the
      next portal update.</div>`;
    return;
  }
  el('feed').innerHTML = items.map(card).join('');
  el('sub').textContent =
    `${items.length} ${items.length === 1 ? 'record' : 'records'}`;
}

async function load() {
  const cached = sessionStorage.getItem(CK + active);
  if (cached) render(JSON.parse(cached)); else skeleton();

  try {
    const res = await fetch(`/api/feed?filter=${active}&limit=60`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    sessionStorage.setItem(CK + active, JSON.stringify(data.items));
    render(data.items);
  } catch (e) {
    if (!cached) {
      el('feed').innerHTML = `<div class="empty"><b>Can't reach the feed</b>
        Check your connection and pull down to retry.</div>`;
    }
    el('sub').textContent = 'Showing saved data';
  }
}

async function health() {
  try {
    const h = await (await fetch('/api/health')).json();
    el('srcs').textContent = `${h.records} tracked`;
  } catch { el('srcs').textContent = 'Offline'; }
}

// Pull to refresh. Only arms at true scroll top so it never fights the
// browser's own overscroll during a normal upward flick.
let y0 = null;
addEventListener('touchstart', e => {
  y0 = scrollY <= 0 ? e.touches[0].clientY : null;
}, { passive: true });
addEventListener('touchend', e => {
  if (y0 !== null && e.changedTouches[0].clientY - y0 > 90) load();
  y0 = null;
}, { passive: true });

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { load(); health(); }
});

chips();
load();
health();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
