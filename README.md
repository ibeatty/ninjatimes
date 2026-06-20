# 🥷 Ninja Times

An athlete-centric view of **World Ninja League (WNL) Championship** run orders. Pick the
athletes you care about and see their wave, wave start time, and run-order position across
Stage 1, Stage 2, Stage 3, and the Discipline Circuit — in one mobile-friendly table you can
sort, filter, and group by day.

The official [WNL run-order pages](https://worldninjaleague.org/run-orders/) only let you
browse one rig at a time, with no search by athlete. Ninja Times scrapes them and flips the
data around to be **per athlete**.

- **Live site:** https://ianbeatty.com/ninjatimes/
- **How fresh:** a GitHub Action re-scrapes every ~10 minutes during the event; the page shows
  the last-updated time and has a Refresh button.

See [`PROBLEM_STATEMENT.md`](PROBLEM_STATEMENT.md) for background and
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the design.

## How it works

```
GitHub Actions (cron) ─▶ scraper/scrape.py
   ├─ reads the WNL master schedule table  (worldninjaleague.org/tier-1-championships/)
   ├─ for each rig page, follows the embedded timing.ninjaworks.com iframe and parses it
   ├─ matches against config/athletes.json (your preset list)
   └─ writes site/data/athletes.json
GitHub Pages ─▶ serves site/  (a static page that loads athletes.json and renders the table)
```

The timing data is referer-gated and has no CORS headers, so it can't be fetched from a
browser — all scraping happens server-side inside the Action, which pre-builds the JSON.

## Configure which athletes are tracked

Edit [`config/athletes.json`](config/athletes.json) — a list of full names:

```json
{ "athletes": ["Easton Fletcher", "Mark Satterwhite", "Joshua Brown"] }
```

Use the registered name (e.g. **Joshua**, not Josh). If a name isn't found, the site shows a
"did you mean…" hint. To disambiguate two athletes who share a name, use
`{ "name": "Jack Smith", "id": "232067" }` (the id is the number shown after a name on the
WNL pages). Commit the change and the next refresh picks it up.

Other knobs live in [`config/settings.json`](config/settings.json): the schedule URL, refresh
politeness delay, and `event_day_order` (used to sort/group days correctly — the event runs
Thursday→Tuesday, wrapping the weekend).

## Run locally

```bash
uv run python scraper/scrape.py        # scrape -> site/data/athletes.json
uv run python -m http.server -d site 8765   # then open http://localhost:8765
uv run --group dev pytest              # run the parser tests
```

## Updating for next year (2027)

Rig pages are auto-discovered from the schedule table, so most years you only touch
`config/settings.json`: the `year`, the `schedule_path`, and `event_day_order`. If WNL keeps
the ninjaworks embed pattern, nothing else changes.

---

Not affiliated with the World Ninja League. Data is scraped from public pages for personal use.
