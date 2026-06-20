# Ninja Times — Implementation Plan

A tool that scrapes World Ninja League (WNL) Championship run orders and presents a
mobile-friendly, athlete-centric table for a configured list of athletes. See
[`PROBLEM_STATEMENT.md`](PROBLEM_STATEMENT.md) for the full background and goals.

## 1. How the WNL data is actually served (the key finding)

The run-order data is **not** WNL's own. Each rig page on `worldninjaleague.org` is a thin
WordPress wrapper around an **iframe from a third-party ninja timing platform**:

```
worldninjaleague.org/run-orders/tier-1-stage-1-small/
   └─ <iframe src="https://timing.ninjaworks.com/embed/101/24067/1437/">
```

- **Master index:** `worldninjaleague.org/tier-1-championships/` holds one static HTML table
  (~152 rows) covering **both tiers**: `Division | Tier | Event | Day | Time | Rig | [link]`.
  We auto-discover every rig page from this table (robust to slug changes year to year).
- **Detail:** each `timing.ninjaworks.com` embed is **server-rendered HTML** — repeating
  `<h3>Wave: T1 Mature Kids Male 8 Saturday 3:53PM</h3>` headings, each followed by a table
  (`Name | Division | Coach`). **Run-order position = row order**; names carry a stable
  athlete id, e.g. `Logan Kiselica (235867)`. Stray `Course Tester` rows are filtered out.

### The one hard constraint

The embed is **referer-gated** (returns `403` without `Referer: worldninjaleague.org`) and
sends **no CORS headers / no public JSON API**. A viewer's browser therefore cannot fetch it
directly. **All scraping must happen server-side** — here, inside GitHub Actions, where
setting a `Referer` header is trivial. This is why the tool pre-builds a data file rather
than fetching live from the browser.

## 2. Architecture (100% GitHub, preset-only)

```
GitHub Actions (cron ~10 min  +  manual "Run workflow" button)
        │  runs scraper/scrape.py
        ▼
  ① GET /tier-1-championships/  → parse master schedule table
        → grid of every (tier, division, event, day, overall start time, rig, link)
  ② For each rig page (auto-discovered):
        GET /run-orders/<slug>/  → extract <iframe src="…ninjaworks.com/embed/…">
        GET that embed WITH  Referer: https://worldninjaleague.org/
        → parse wave headings + tables (athlete, division, position)
  ③ Match against config/athletes.json (the preset list)
        → build per-athlete rows, applying TBA / did-not-qualify logic
        → merge with previously published snapshot (persist athletes who already ran)
  ④ Emit site/data/athletes.json + site/data/meta.json
        ▼
  Deploy site/ to GitHub Pages (artifact deploy; Pages source = GitHub Actions)
        ▼
  Viewer browser → index.html + app.js → loads athletes.json
        → preset multi-select + text filter + sortable, mobile-first table
```

No live calls from the viewer; everything is in the pre-built JSON.

## 3. Data model

Each row = one athlete × one event. Eight **displayed** columns:

| field | source | notes |
|---|---|---|
| `athlete` | embed row | name, with `(id)` parsed off for disambiguation |
| `tier` | wave heading / slug | 1 or 2 |
| `division` | embed row/heading | e.g. "Mature Kids Male" |
| `event` | rig page slug | Stage 1 / Stage 2 / Stage 3 / DC |
| `rig` | rig page slug | e.g. "Stage 1 Small", "DC Tall" |
| `wave` | wave heading | number · `TBA` · blank (did-not-qualify) |
| `wave start` | wave heading | "Sat 3:53 PM"; for TBA, the overall start from the schedule |
| `run order` | row position in wave | integer · `n/a` for DC · `TBA` if unposted |

**Removed:** a separate `day` column (it was a hidden helper in the spreadsheet). Day
grouping/sorting is derived from `wave start` via an **internal sort key** the scraper emits
(`sort_key` = day-index-in-event-order × 10000 + minutes-since-midnight). Because the event
runs **Thursday → Tuesday** (wrapping the weekend), a naive day-of-week sort would misorder
Mon/Tue before Thu — the explicit `event_day_order` in `config/settings.json` fixes this and
is the single knob to update next year.

Internal-only fields on each row: `athlete_id`, `sort_key`, `status`
(`posted` / `tba` / `did_not_qualify` / `completed`).

### TBA vs. did-not-qualify (per the problem statement)

- Athlete present in the event's embed → **posted** (real wave / position / time).
- Event embed has that *division* populated but athlete absent → **did_not_qualify**
  (blank wave + start, struck-through in the UI).
- Event embed not yet populated for that division → **tba** (wave/order = `TBA`,
  wave-start = overall start time from the schedule table).

### Persistence (the "athletes disappear after they run" quirk)

The live page shows upcoming athletes only (dropping those who have run). The scraper fetches
the previously published `athletes.json` over HTTP and merges: a row that was `posted` and has
now vanished is retained and marked `completed`, so it never disappears from our view.

## 4. Tech stack & repo layout

- **Scraper:** Python (managed by `uv`) — `httpx` + `beautifulsoup4` + `lxml`. Pure parsing
  functions in `parse.py` so they're unit-testable against saved fixtures.
- **Front-end:** vanilla HTML/CSS/JS, mobile-first, zero framework. Click-to-sort headers,
  preset multi-select + "Select all", comma-separated text filter, shareable URL params.
- **Automation/hosting:** GitHub Actions (cron + `workflow_dispatch`) → GitHub Pages.

```
ninjatimes/
  PROBLEM_STATEMENT.md   IMPLEMENTATION_PLAN.md   README.md
  pyproject.toml                       # uv project (deps)
  config/
    settings.json                      # year, base URLs, cadence, day order
    athletes.json                      # the preset list (edit anytime)
  scraper/
    scrape.py                          # orchestration + HTTP + I/O
    parse.py                           # pure HTML→data parsing
    tests/                             # validated against the Google Sheet rows
  site/
    index.html  app.js  styles.css
    data/athletes.json  data/meta.json # generated (gitignored; built in CI)
  .github/workflows/refresh.yml
```

## 5. Build milestones

- **M0 — Scaffold** ✅ repo skeleton + this plan.
- **M1 — Scraper:** produce correct `athletes.json`. **Test oracle:** the hand-verified sheet
  rows for *Easton Fletcher, Mark Satterwhite, Josh Brown, Kane Casillas* (incl. TBA and DC
  `n/a` cases). Confirms parsing against real, in-progress data.
- **M2 — Front-end:** table + multi-select + text filter + sorting + TBA/struck-through +
  "last updated" stamp. Runs locally.
- **M3 — Automation:** Actions cron + Pages deploy → public URL live.
- **M4 — Polish:** mobile tuning, sort presets mirroring the sheet's tabs, requested filters.

## 6. Operator (you) steps

`gh` is authenticated, so the repo, code, and workflow are pushed for you. The only actions
that must happen in the GitHub web UI:

1. **Settings → Pages → Source = "GitHub Actions".**
2. **Settings → Actions → General → Workflow permissions →** allow read/write (the deploy uses
   job-level permissions, but confirm this isn't org-restricted).
3. Edit `config/athletes.json` to add/remove tracked athletes (commit, or ask me).

No accounts to create, no Cloudflare, no billing — all within GitHub's free tier.

## 7. Next-year (2027) adaptability

Rig pages are auto-discovered from the schedule table; base URLs, the schedule path, and
`event_day_order` live in `config/settings.json`. Next year, typically just update those few
config values. If WNL keeps the ninjaworks embed pattern (very likely), nothing else changes.

## 8. Known risks / edge cases handled

Referer gate or markup changes (isolated in `parse.py`, logged on failure) · duplicate athlete
names (disambiguated by `(id)` + division) · `Course Tester` rows (filtered) · wave headings
occasionally missing day/time (fall back to schedule) · Actions cron delays (cosmetic; the
"last updated" stamp shows true freshness) · partial/posting-in-progress stages (per-division
populated check drives TBA vs. did-not-qualify).
