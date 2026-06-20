'use strict';

const DATA_URL = 'data/athletes.json';
const EVENT_ORDER = ['Stage 1', 'Stage 2', 'Stage 3', 'DC'];

const state = {
  data: null,
  selected: new Set(),     // chip-selected athlete names
  filter: '',              // free-text filter
  sort: 'athlete',
  hideDnq: false,
  hideTba: false,
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
  }
  applyUrlParams();
  initChips();
  renderFreshness();
  renderUnmatched();
  render();
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
      ? ' — did you mean ' + u.suggestions.map((s) =>
          `<a data-name="${esc(s)}">${esc(s)}</a>`).join(', ') + '?'
      : '';
    return `<b>${esc(u.name)}</b>${sugg}`;
  });
  banner.innerHTML = `No current data for: ${parts.join('; ')}`;
  banner.querySelectorAll('a[data-name]').forEach((a) => {
    a.addEventListener('click', () => {
      $('#filter').value = a.dataset.name;
      state.filter = a.dataset.name.toLowerCase();
      render();
    });
  });
}

// --- filtering & sorting ----------------------------------------------------

function visibleRows() {
  const terms = state.filter.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  let rows = state.data.rows.slice();

  if (terms.length) {
    rows = rows.filter((r) => terms.some((t) => r.athlete.toLowerCase().includes(t)));
  } else {
    rows = rows.filter((r) => state.selected.has(r.athlete));
  }
  if (state.hideDnq) rows = rows.filter((r) => r.status !== 'did_not_qualify');
  if (state.hideTba) rows = rows.filter((r) => r.status !== 'tba');

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
  { key: 'tier', label: 'Tier', html: (r) => `<span class="tier-badge">T${esc(r.tier)}</span>` },
  { key: 'division', label: 'Division' },
  { key: 'event', label: 'Event', html: (r) => `${esc(r.event)}${statusPill(r)}` },
  { key: 'rig', label: 'Rig' },
  { key: 'wave', label: 'Wave', cls: 'num' },
  { key: 'wave_start', label: 'Wave start' },
  { key: 'run_order', label: 'Run order', cls: 'num' },
];

function statusPill(r) {
  if (r.status === 'did_not_qualify') return ' <span class="pill pill-dnq keep">did not qualify</span>';
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

  let body = '';
  const kind = groupKind();
  let lastGroup = null;
  for (const r of rows) {
    if (kind) {
      const g = groupLabel(r, kind);
      if (g !== lastGroup) {
        body += `<tr class="daygroup"><td colspan="${COLUMNS.length}">${esc(g)}</td></tr>`;
        lastGroup = g;
      }
    }
    body += `<tr class="${rowClass(r)}">` + COLUMNS.map((c) => {
      const content = c.html ? c.html(r) : esc(r[c.key]);
      return `<td class="${c.cls || ''}" data-label="${esc(c.label)}">${content}</td>`;
    }).join('') + '</tr>';
  }

  $('#table-wrap').innerHTML = rows.length
    ? `<table>${head}<tbody>${body}</tbody></table>` : '';

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
  $('#filter').addEventListener('input', (e) => { state.filter = e.target.value.toLowerCase(); render(); });
  $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
  $('#hide-dnq').addEventListener('change', (e) => { state.hideDnq = e.target.checked; render(); });
  $('#hide-tba').addEventListener('change', (e) => { state.hideTba = e.target.checked; render(); });
  load();
}

document.addEventListener('DOMContentLoaded', init);
