'use strict';

const DATA_URL = 'data/athletes.json';
const EVENT_ORDER = ['Stage 1', 'Stage 2', 'Stage 3', 'DC', 'Head to Head'];

// Tier badge: numeric tiers show as T1/T2; H2H carries tier "H2" already.
const tierLabel = (r) => (typeof r.tier === 'number' ? 'T' + r.tier : esc(r.tier));

const state = {
  data: null,
  selected: new Set(),     // chip-selected athlete names
  sort: 'wave_start',      // default: chronological, grouped by day
  hideDnq: true,
  hideTba: false,
  hideCompleted: true,     // hide rows whose wave is done (every athlete in it has run)
};

// Primary sort modes; each drives a grouping. Other columns can be clicked to
// sort ad hoc (ungrouped). Sorting always breaks ties by wave start (sort_key).
const SORT_MODES = {
  athlete: { group: 'athlete' },     // group by athlete, then by wave start
  wave_start: { group: 'day' },      // group by day of week, then by time
  event: { group: 'event' },         // group by event, then by wave start
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// --- load -------------------------------------------------------------------

async function load() {
  const prevGenerated = state.data && state.data.generated_at;
  setRefreshing(true);
  $('#updated').textContent = 'Loading…';
  try {
    const res = await fetch(`${DATA_URL}?cb=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
  } catch (err) {
    $('#updated').textContent = 'Could not load data';
    $('#table-wrap').innerHTML =
      `<div class="empty">Failed to load run-order data (${esc(err.message)}).<br>
       Try refreshing in a moment.</div>`;
    return;
  } finally {
    setRefreshing(false);
  }
  applyUrlParams();
  syncControls();
  initChips();
  renderFreshness();
  renderUnmatched();
  render();
  // Acknowledge the refresh even when the data is unchanged, so a successful
  // click is never mistaken for a dead button.
  flashUpdated(prevGenerated && state.data.generated_at === prevGenerated);
}

// Busy state for the Refresh button: disabled + label swap while a load runs.
function setRefreshing(on) {
  const btn = $('#reload');
  if (!btn) return;
  btn.disabled = on;
  btn.classList.toggle('loading', on);
  btn.textContent = on ? '↻ Refreshing…' : '↻ Refresh';
}

// Briefly highlight the freshness label so a completed refresh is visible even
// when the rebuilt data is byte-for-byte identical to what was already shown.
function flashUpdated(unchanged) {
  const el = $('#updated');
  if (!el) return;
  el.classList.remove('flash');
  void el.offsetWidth;            // restart the CSS animation
  el.classList.add('flash');
  if (unchanged) el.title = `Already up to date (checked ${new Date().toLocaleTimeString()})`;
}

// Reflect current state into the controls (so defaults + URL params show correctly).
function syncControls() {
  $('#sort').value = state.sort;
  $('#hide-dnq').checked = state.hideDnq;
  $('#hide-tba').checked = state.hideTba;
  $('#hide-completed').checked = state.hideCompleted;
}

// --- url params -------------------------------------------------------------

function applyUrlParams() {
  const p = new URLSearchParams(location.search);
  const names = p.get('athletes');
  if (names) {
    state._urlAthletes = names.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  }
  if (p.get('sort') && SORT_MODES[p.get('sort')]) {
    state.sort = p.get('sort');
    $('#sort').value = state.sort;
  }
}

// --- chips ------------------------------------------------------------------

function presetNames() {
  // Union of configured presets and any athlete actually present in the rows.
  const names = new Set((state.data.presets || []));
  for (const r of state.data.rows) names.add(r.athlete);
  return [...names].sort((a, b) => a.localeCompare(b));
}

function unmatchedNames() {
  return new Set((state.data.unmatched || []).map((u) => u.name.toLowerCase()));
}

function initChips() {
  const wrap = $('#chips');
  const unmatched = unmatchedNames();
  const present = new Set(state.data.rows.map((r) => r.athlete.toLowerCase()));
  const names = presetNames();

  // default selection: everything present (or the ?athletes= subset)
  state.selected = new Set();
  for (const n of names) {
    const lower = n.toLowerCase();
    if (!present.has(lower)) continue;
    if (state._urlAthletes && !state._urlAthletes.includes(lower)) continue;
    state.selected.add(n);
  }

  wrap.innerHTML = '';
  for (const n of names) {
    const lower = n.toLowerCase();
    const isUnmatched = unmatched.has(lower) || !present.has(lower);
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip';
    chip.textContent = n;
    chip.setAttribute('aria-pressed', state.selected.has(n) ? 'true' : 'false');
    if (isUnmatched) chip.setAttribute('data-unmatched', 'true');
    chip.title = isUnmatched ? 'No current data for this name' : '';
    chip.addEventListener('click', () => {
      if (state.selected.has(n)) state.selected.delete(n);
      else state.selected.add(n);
      chip.setAttribute('aria-pressed', state.selected.has(n) ? 'true' : 'false');
      render();
    });
    wrap.appendChild(chip);
  }
}

// --- freshness --------------------------------------------------------------

function renderFreshness() {
  const el = $('#updated');
  const iso = state.data.generated_at;
  if (!iso) { el.textContent = ''; return; }
  const when = new Date(iso);
  const mins = Math.round((Date.now() - when.getTime()) / 60000);
  const rel = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago`
    : `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
  const local = when.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  el.textContent = `Updated ${local} (${rel})`;
  el.title = when.toString();
  el.classList.toggle('stale', mins >= 25);
}

// --- unmatched banner -------------------------------------------------------

function renderUnmatched() {
  const banner = $('#unmatched');
  const list = state.data.unmatched || [];
  if (!list.length) { banner.hidden = true; return; }
  banner.hidden = false;
  const parts = list.map((u) => {
    const sugg = (u.suggestions || []).length
      ? ` — did you mean ${u.suggestions.map(esc).join(', ')}?`
      : '';
    return `<b>${esc(u.name)}</b>${sugg}`;
  });
  banner.innerHTML = `No current data for: ${parts.join('; ')}`;
}

// --- filtering & sorting ----------------------------------------------------

function visibleRows() {
  let rows = state.data.rows.filter((r) => state.selected.has(r.athlete));
  if (state.hideDnq) rows = rows.filter((r) => r.status !== 'did_not_qualify');
  if (state.hideTba) rows = rows.filter((r) => r.status !== 'tba');
  if (state.hideCompleted) rows = rows.filter((r) => r.wave_state !== 'over');

  rows.sort(comparator(state.sort));
  return rows;
}

function comparator(mode) {
  const evIdx = (r) => { const i = EVENT_ORDER.indexOf(r.event); return i < 0 ? 99 : i; };
  const num = (v) => { const n = parseInt(v, 10); return Number.isNaN(n) ? Infinity : n; };
  const val = (r) => {
    switch (mode) {
      case 'event': return [evIdx(r), r.sort_key];
      case 'wave_start': return [r.sort_key];
      case 'division': return [r.division.toLowerCase(), r.sort_key];
      case 'tier': return [r.tier, r.sort_key];
      case 'rig': return [r.rig.toLowerCase(), r.sort_key];
      case 'wave': return [num(r.wave), r.sort_key];
      case 'run_order': return [num(r.run_order), r.sort_key];
      case 'place': return [num(r.place), r.sort_key];
      case 'athlete':
      default: return [r.athlete.toLowerCase(), r.sort_key];
    }
  };
  return (a, b) => {
    const va = val(a), vb = val(b);
    for (let i = 0; i < Math.max(va.length, vb.length); i++) {
      const x = va[i], y = vb[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      if (typeof x === 'number' && typeof y === 'number') { if (x !== y) return x - y; }
      else { const c = String(x).localeCompare(String(y)); if (c) return c; }
    }
    return 0;
  };
}

function groupKind() { return (SORT_MODES[state.sort] || {}).group || null; }

function groupLabel(r, kind) {
  if (kind === 'athlete') return r.athlete;
  if (kind === 'event') return r.event;
  if (kind === 'day') return dayLabel(r);
  return null;
}

function dayLabel(r) {
  const order = state.data.event_day_order || [];
  const idx = Math.floor(r.sort_key / 10000);
  return order[idx] || 'Unscheduled';
}

// --- render -----------------------------------------------------------------

const COLUMNS = [
  { key: 'athlete', label: 'Athlete', cls: 'athlete-cell' },
  { key: 'tier', label: 'Tier', html: (r) => `<span class="tier-badge">${tierLabel(r)}</span>` },
  { key: 'division', label: 'Division' },
  { key: 'event', label: 'Event', html: (r) => `${esc(r.event)}${statusPill(r)}` },
  { key: 'rig', label: 'Rig' },
  { key: 'wave', label: 'Wave', cls: 'num' },
  { key: 'wave_start', label: 'Wave start', html: (r) => `${waveIcon(r)}${esc(r.wave_start)}` },
  { key: 'run_order', label: 'Run order', cls: 'num', html: (r) => runOrderCell(r) },
  { key: 'place', label: 'Place', cls: 'num', html: (r) => placeCell(r) },
];

function placeCell(r) {
  if (r.event_final && r.place) {            // finalized placement
    const medal = { '1': '🥇', '2': '🥈', '3': '🥉' }[String(r.place)] || '';
    return `<span class="place">${medal ? medal + ' ' : ''}${esc(r.place)}</span>`;
  }
  if (r.has_run && !r.event_final) {         // ran, but the event isn't over
    return '<span class="pending" title="Finished — awaiting final standings">⏳</span>';
  }
  return '';
}

// Icon shown immediately before the wave-start time.
function waveIcon(r) {
  if (r.wave_state === 'in_progress') return '<span class="inprogress" title="Wave in progress"></span> ';
  if (r.wave_state === 'over') return '<span class="wave-done" title="Wave complete">✓</span> ';
  return '';
}

// Run order with a green check once the athlete has run (table cell).
function runOrderCell(r) {
  const ro = String(r.run_order ?? '');
  const check = (r.has_run && /^\d+$/.test(ro)) ? ' <span class="ran-check" title="Has run">✓</span>' : '';
  return `${esc(ro)}${check}`;
}

// Run order for the compact card (#N, with the same check).
function runOrderShort(r) {
  const ro = String(r.run_order ?? '');
  if (!ro || ro === 'n/a') return '';
  if (ro === 'TBA') return 'TBA';
  return '#' + esc(ro) + (r.has_run ? ' <span class="ran-check">✓</span>' : '');
}

// Compact mobile card: name + three dense lines (no labels).
function cardHtml(r) {
  const l1 = `${tierLabel(r)} &middot; ${esc(r.division)} &middot; ${esc(r.event)}${statusPill(r)}`;
  const l2 = [esc(r.rig), r.wave ? `Wave ${esc(r.wave)}` : ''].filter(Boolean).join(' &middot; ');
  const waveStart = r.wave_start ? `${waveIcon(r)}${esc(r.wave_start)}` : '';
  const l3 = [waveStart, runOrderShort(r), placeCell(r)].filter(Boolean).join(' &middot; ');
  return `<div class="card ${rowClass(r)}">
    <div class="card-name">${esc(r.athlete)}</div>
    <div class="card-line">${l1}</div>
    ${l2 ? `<div class="card-line dim">${l2}</div>` : ''}
    ${l3 ? `<div class="card-line">${l3}</div>` : ''}
  </div>`;
}

function statusPill(r) {
  if (r.status === 'did_not_qualify') return ' <span class="pill pill-dnq keep">DNQ</span>';
  if (r.status === 'completed') return ' <span class="pill pill-done keep">ran</span>';
  return '';
}

function rowClass(r) {
  return { posted: '', tba: 'row-tba', did_not_qualify: 'row-dnq', completed: 'row-done' }[r.status] || '';
}

function render() {
  const rows = visibleRows();
  $('#empty').hidden = rows.length > 0;

  const head = '<thead><tr>' + COLUMNS.map((c) => {
    const active = state.sort === c.key;
    const arrow = active ? '<span class="arrow">▾</span>' : '';
    return `<th data-key="${c.key}">${esc(c.label)}${arrow}</th>`;
  }).join('') + '</tr></thead>';

  let body = '';      // desktop table rows
  let cards = '';     // mobile cards
  const kind = groupKind();
  let lastGroup = null;
  for (const r of rows) {
    if (kind) {
      const g = groupLabel(r, kind);
      if (g !== lastGroup) {
        body += `<tr class="daygroup"><td colspan="${COLUMNS.length}">${esc(g)}</td></tr>`;
        cards += `<div class="group-head">${esc(g)}</div>`;
        lastGroup = g;
      }
    }
    body += `<tr class="${rowClass(r)}">` + COLUMNS.map((c) => {
      const content = c.html ? c.html(r) : esc(r[c.key]);
      return `<td class="${c.cls || ''}" data-label="${esc(c.label)}">${content}</td>`;
    }).join('') + '</tr>';
    cards += cardHtml(r);
  }

  $('#table-wrap').innerHTML = rows.length
    ? `<table>${head}<tbody>${body}</tbody></table><div class="cards">${cards}</div>` : '';

  // summary
  const athletes = new Set(rows.map((r) => r.athlete));
  $('#summary').textContent =
    `${rows.length} row${rows.length === 1 ? '' : 's'} · ${athletes.size} athlete${athletes.size === 1 ? '' : 's'}`;

  // header sort handlers
  $('#table-wrap').querySelectorAll('th[data-key]').forEach((th) => {
    th.addEventListener('click', () => {
      state.sort = th.dataset.key;
      if (SORT_MODES[state.sort]) $('#sort').value = state.sort;
      render();
    });
  });
}

// --- wire up controls -------------------------------------------------------

function init() {
  $('#reload').addEventListener('click', load);
  $('#select-all').addEventListener('click', () => {
    presetNames().forEach((n) => state.selected.add(n));
    document.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', 'true'));
    render();
  });
  $('#select-none').addEventListener('click', () => {
    state.selected.clear();
    document.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', 'false'));
    render();
  });
  $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
  $('#hide-dnq').addEventListener('change', (e) => { state.hideDnq = e.target.checked; render(); });
  $('#hide-tba').addEventListener('change', (e) => { state.hideTba = e.target.checked; render(); });
  $('#hide-completed').addEventListener('change', (e) => { state.hideCompleted = e.target.checked; render(); });
  // Keep the "updated … ago" label ticking without a reload.
  setInterval(() => { if (state.data) renderFreshness(); }, 60000);
  load();
}

document.addEventListener('DOMContentLoaded', init);
