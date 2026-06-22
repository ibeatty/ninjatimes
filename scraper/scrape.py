"""Ninja Times scraper.

Fetches the WNL master schedule + every ninjaworks rig embed, then builds an
athlete-centric data file for the configured preset list.

Usage:
    uv run python scraper/scrape.py                 # full run -> site/data/
    uv run python scraper/scrape.py --max-pages 2   # dev: only scrape a couple rigs
    uv run python scraper/scrape.py --no-persist    # ignore previously published data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import urljoin

# Allow running as a script (python scraper/scrape.py) or as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUT_DIR = ROOT / "site" / "data"

log = logging.getLogger("ninjatimes")


# --- config -----------------------------------------------------------------

def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_athletes(cfg: dict) -> list[dict]:
    """Normalize the preset list into [{name, name_key, id?}, ...]."""
    out = []
    for entry in cfg.get("athletes", []):
        if isinstance(entry, str):
            name = entry
            athlete_id = None
        else:
            name = entry.get("name", "")
            athlete_id = entry.get("id")
        if not name.strip():
            continue
        out.append({"name": P.clean(name), "name_key": P.norm(name), "id": athlete_id})
    return out


# --- HTTP -------------------------------------------------------------------

RETRY_STATUS = {"403", "408", "429", "500", "502", "503", "504"}


class Fetcher:
    """Fetches pages via `curl`, with retries.

    We shell out to curl rather than using a Python HTTP client because
    Cloudflare (in front of worldninjaleague.org) fingerprints the TLS/HTTP
    client: from a datacenter IP it blocks Python clients (httpx) with 403 but
    lets curl through with 200. curl works from both GitHub Actions and locally.
    timing.ninjaworks.com is not behind Cloudflare but referer-gates its embeds.
    """

    def __init__(self, settings: dict):
        self.ua = settings.get("user_agent", "Mozilla/5.0")
        self.referer = settings.get("referer", "")
        self.delay = float(settings.get("request_delay_seconds", 0.75))
        self.timeout = int(settings.get("request_timeout_seconds", 30))
        self.max_retries = int(settings.get("max_retries", 4))

    def get(self, url: str, referer: bool = False) -> str:
        cmd = ["curl", "-sSL", "--compressed", "-A", self.ua,
               "--max-time", str(self.timeout)]
        if referer and self.referer:
            cmd += ["-e", self.referer]
        cmd += ["-w", "\n%{http_code}", url]

        last = ""
        for attempt in range(self.max_retries):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                last = f"curl exit {proc.returncode}: {proc.stderr.strip()[:120]}"
            else:
                body, _, code = proc.stdout.rpartition("\n")
                code = code.strip()
                if code.startswith("2"):
                    time.sleep(self.delay)
                    return body
                last = f"HTTP {code}"
                if code not in RETRY_STATUS:
                    raise RuntimeError(f"{url}: {last}")
            wait = 2 * (2 ** attempt)
            log.info("  %s -> %s, retrying in %ss", url, last, wait)
            time.sleep(wait)
        raise RuntimeError(f"{url}: {last}")

    def close(self):
        pass


# --- sort key ---------------------------------------------------------------

def sort_key(day_order: list[str], weekday: str | None, minutes: int | None) -> int:
    """Chronological key honoring the event's true day order (Thu..Tue)."""
    di = day_order.index(weekday) if weekday in day_order else len(day_order) + 1
    return di * 10000 + (minutes if minutes is not None else 0)


def sort_key_from_wave_start(day_order: list[str], wave_start: str) -> int:
    """Same key, derived from a display string like 'Sat 3:53 PM' (used for backfill)."""
    parts = (wave_start or "").split()
    abbr = parts[0][:3].title() if parts else ""
    weekday = next((d for d in day_order if d[:3] == abbr), None)
    clk = P.parse_clock(wave_start)
    return sort_key(day_order, weekday, clk[1] if clk else None)


def wave_start_str(weekday: str | None, time_disp: str | None) -> str:
    wd = P.weekday_abbr(weekday)
    if wd and time_disp:
        return f"{wd} {time_disp}"
    return time_disp or ""


def suggest_names(name: str, all_names: list[str]) -> list[str]:
    """'Did you mean' hints for an unmatched preset name."""
    hits = get_close_matches(name, all_names, n=4, cutoff=0.6)
    parts = P.norm(name).split()
    if parts:
        surname = parts[-1]
        extra = [n for n in all_names
                 if P.norm(n).split()[-1:] == [surname] and n not in hits]
        hits = (hits + extra)[:5]
    return hits


# --- core build -------------------------------------------------------------

def fetch_pages(fetcher: Fetcher, embed_src: str, max_pages: int = 25) -> list[str]:
    """Fetch a ninjaworks embed and follow its pagination, returning each page's HTML.

    Long run orders split across pages (?run_order_competition_id=…&page=N); without
    following them, later waves — i.e. later run-order positions — are never seen.
    """
    htmls: list[str] = []
    seen: set[str] = set()
    queue = [embed_src]
    while queue and len(htmls) < max_pages:
        url = queue.pop(0)
        base = url.split("#")[0]
        if base in seen:
            continue
        seen.add(base)
        try:
            html = fetcher.get(url, referer=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("page fetch failed (%s): %s", url, exc)
            continue
        htmls.append(html)
        for href in P.parse_pagination_urls(html):
            nxt = urljoin(embed_src, href)
            if nxt.split("#")[0] not in seen:
                queue.append(nxt)
    return htmls


def build(settings: dict, athletes: list[dict], fetcher: Fetcher,
          max_pages: int | None = None) -> dict:
    base = settings["base_url"]
    day_order = settings.get("event_day_order", [])
    want_events = settings.get("events", ["Stage 1", "Stage 2", "Stage 3", "DC"])
    # Only these events have a qualifying cut, so only here does an absence mean
    # "did not qualify". Stage 1 and DC are universal — an absence there is someone
    # who has already run (and dropped off the live page) or isn't posted yet.
    qualifying = set(settings.get("qualifying_events", ["Stage 2", "Stage 3"]))
    include_h2h = settings.get("include_h2h", False)

    # 1) schedule -> grid + rig discovery
    sched_url = urljoin(base, settings["schedule_path"])
    log.info("Fetching schedule: %s", sched_url)
    sched_html = fetcher.get(sched_url)
    entries = P.parse_schedule_table(sched_html, base_url=base)
    log.info("Schedule rows parsed: %d", len(entries))

    grid: dict[tuple, P.ScheduleEntry] = {}
    events_for_div: dict[tuple, set] = {}
    rig_slugs: dict[str, str] = {}  # slug -> absolute url
    for e in entries:
        if e.event in want_events and e.tier and e.division_key:
            grid[(e.tier, e.division_key, e.event)] = e
            events_for_div.setdefault((e.tier, e.division_key), set()).add(e.event)
        if e.slug and e.url:
            info = P.parse_rig_slug(e.slug)
            if info and (info["event"] in want_events or (include_h2h and info["event"] == "H2H")):
                rig_slugs.setdefault(e.slug, urljoin(base, e.url))

    slugs = sorted(rig_slugs)
    if max_pages:
        slugs = slugs[:max_pages]
    log.info("Rig pages to scrape: %d", len(slugs))

    # 2) scrape each rig embed -> appearances
    appearances: list[dict] = []
    populated: set[tuple] = set()   # (event, division_key) present in any embed
    for slug in slugs:
        info = P.parse_rig_slug(slug) or {}
        event, rig = info.get("event"), info.get("rig")
        rig_url = rig_slugs[slug]
        try:
            embed_src = P.extract_embed_src(fetcher.get(rig_url))
            if not embed_src:
                log.warning("No embed iframe on %s", rig_url)
                continue
            pages = fetch_pages(fetcher, embed_src)
        except Exception as exc:  # noqa: BLE001 — one bad page shouldn't sink the run
            log.warning("Failed rig %s: %s", slug, exc)
            continue
        seen_ids: set[str] = set()
        n_waves = n_rows = 0
        for html in pages:
            page = P.parse_embed(html)
            n_waves += page.wave_count
            for r in page.rows:
                if r.athlete_id and r.athlete_id in seen_ids:
                    continue   # row repeated across the page boundary
                if r.athlete_id:
                    seen_ids.add(r.athlete_id)
                tier = r.tier or info.get("tier")
                appearances.append({
                    "name": r.name, "name_key": r.name_key, "athlete_id": r.athlete_id,
                    "tier": tier, "division": r.division, "division_key": r.division_key,
                    "event": event, "rig": rig, "wave": r.wave, "weekday": r.weekday,
                    "time_disp": r.time_disp, "time_min": r.time_min, "position": r.position,
                })
                populated.add((event, r.division_key))
                n_rows += 1
        log.info("  %-26s %2d waves, %3d athletes (%d page%s)",
                 slug, n_waves, n_rows, len(pages), "" if len(pages) == 1 else "s")

    # 3) name index
    name_index: dict[str, list[dict]] = {}
    for a in appearances:
        name_index.setdefault(a["name_key"], []).append(a)

    # 4) athlete-centric rows
    all_names = sorted({a["name"] for a in appearances})
    rows: list[dict] = []
    unmatched: list[dict] = []
    for spec in athletes:
        matches = name_index.get(spec["name_key"], [])
        if spec["id"]:
            matches = [m for m in matches if m["athlete_id"] == spec["id"]]
        if not matches:
            unmatched.append({"name": spec["name"],
                              "suggestions": suggest_names(spec["name"], all_names)})
            continue
        # split by athlete_id so two people sharing a name stay distinct
        by_person: dict[str, list[dict]] = {}
        for m in matches:
            by_person.setdefault(m["athlete_id"] or m["name_key"], []).append(m)

        for pid, group in by_person.items():
            display = group[0]["name"]
            person_id = group[0]["athlete_id"]
            tier = group[0]["tier"]
            div_key = Counter(g["division_key"] for g in group).most_common(1)[0][0]
            division = next(g["division"] for g in group if g["division_key"] == div_key)
            posted = {g["event"]: g for g in group}

            div_events = events_for_div.get((tier, div_key), set())
            emit_events = [ev for ev in want_events if ev in div_events or ev in posted]
            for event in emit_events:
                sched = grid.get((tier, div_key, event))
                if event in posted:
                    a = posted[event]
                    is_dc = event == "DC"
                    wd, tmin = a["weekday"], a["time_min"]
                    ws = wave_start_str(a["weekday"], a["time_disp"])
                    if not ws and sched:  # heading lacked day/time -> schedule fallback
                        ws = wave_start_str(sched.weekday, sched.start_disp)
                        wd, tmin = sched.weekday, sched.start_min
                    rows.append({
                        "athlete": display, "athlete_id": person_id,
                        "tier": tier, "division": division, "event": event,
                        "rig": a["rig"] or (sched.rig if sched else ""),
                        "wave": str(a["wave"]) if a["wave"] is not None else "",
                        "wave_start": ws,
                        "run_order": "n/a" if is_dc else str(a["position"]),
                        "status": "posted",
                        "sort_key": sort_key(day_order, wd, tmin),
                    })
                elif sched is not None:
                    if event in qualifying and (event, div_key) in populated:
                        # qualifying stage posted for this division, athlete absent
                        # -> did not qualify
                        rows.append({
                            "athlete": display, "athlete_id": person_id,
                            "tier": tier, "division": division, "event": event,
                            "rig": sched.rig, "wave": "", "wave_start": "",
                            "run_order": "", "status": "did_not_qualify",
                            "sort_key": sort_key(day_order, sched.weekday, sched.start_min),
                        })
                    else:
                        # not yet posted -> TBA, show overall event start time
                        rows.append({
                            "athlete": display, "athlete_id": person_id,
                            "tier": tier, "division": division, "event": event,
                            "rig": sched.rig,
                            "wave": "TBA",
                            "wave_start": wave_start_str(sched.weekday, sched.start_disp),
                            "run_order": "TBA", "status": "tba",
                            "sort_key": sort_key(day_order, sched.weekday, sched.start_min),
                        })

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_day_order": day_order,
        "presets": [a["name"] for a in athletes],
        "rows": rows,
        "unmatched": unmatched,
        "counts": {
            "appearances": len(appearances),
            "rows": len(rows),
            "rig_pages": len(slugs),
            "matched_athletes": len({r["athlete"] for r in rows}),
        },
    }
    # How many competitors remain in each (tier, division, event) run order — 0
    # means everyone has run (a precondition for finalizing that event's results).
    run_remaining = Counter((a["tier"], a["division_key"], a["event"]) for a in appearances)
    result["_run_remaining"] = {f"{t}|{d}|{e}": n for (t, d, e), n in run_remaining.items()}
    # Lowest wave number still in each run order = the wave currently running. An
    # athlete whose wave equals it is mid-wave; a higher wave is still upcoming.
    current_wave: dict[str, int] = {}
    for a in appearances:
        if a["wave"] is None:
            continue
        k = f'{a["tier"]}|{a["division_key"]}|{a["event"]}'
        if k not in current_wave or a["wave"] < current_wave[k]:
            current_wave[k] = a["wave"]
    result["_current_wave"] = current_wave
    # When is each (tier, division, event) scheduled to FINISH? Use the latest end
    # across its schedule rows — DC spans multiple days, so a combo has several.
    # Past that end, the event is over regardless of run-order/results state: the
    # robust "done" signal that a partial DC Overall or a restored run order can't.
    event_dates = settings.get("event_dates", {})
    offset = settings.get("event_utc_offset", "-04:00")
    now = datetime.now(timezone.utc)
    combo_end: dict[str, datetime] = {}
    for e in entries:
        if not (e.tier and e.division_key and e.event and e.weekday and e.end_min is not None):
            continue
        date = event_dates.get(e.weekday)
        if not date:
            continue
        try:
            end_dt = datetime.fromisoformat(
                f"{date}T{e.end_min // 60:02d}:{e.end_min % 60:02d}:00{offset}")
        except ValueError:
            continue
        k = f"{e.tier}|{e.division_key}|{e.event}"
        if k not in combo_end or end_dt > combo_end[k]:
            combo_end[k] = end_dt
    result["_past_end"] = {k: now >= dt for k, dt in combo_end.items()}
    return result


# --- persistence merge ------------------------------------------------------

def merge_previous(result: dict, prev: dict | None) -> dict:
    """Keep athletes who have already run (dropped from the live page) as 'completed'."""
    if not prev or "rows" not in prev:
        return result
    cur = result["rows"]
    prev_posted = {
        (P.norm(r["athlete"]), r["event"]): r
        for r in prev["rows"]
        if r.get("status") in ("posted", "completed") and r.get("wave") not in ("", "TBA", None)
    }
    cur_index = {(P.norm(r["athlete"]), r["event"]): r for r in cur}

    for key, prow in prev_posted.items():
        crow = cur_index.get(key)
        if crow is None:
            # vanished entirely -> re-add as completed
            kept = dict(prow)
            kept["status"] = "completed"
            cur.append(kept)
        elif crow["status"] in ("tba", "did_not_qualify"):
            # regressed (they ran, source dropped them) -> restore prior assignment
            crow.update({
                "wave": prow["wave"], "wave_start": prow["wave_start"],
                "run_order": prow["run_order"], "rig": prow.get("rig", crow["rig"]),
                "status": "completed",
                "sort_key": prow.get("sort_key", crow["sort_key"]),
            })
    result["counts"]["rows"] = len(cur)
    return result


def apply_backfill(result: dict, backfill: dict | None, day_order: list[str]) -> dict:
    """Overlay manually supplied rows for events that ran before scraping began.

    Only overrides a non-posted (TBA / absent) live result; never live posted data.
    Matched by athlete name + event.
    """
    bf_rows = (backfill or {}).get("rows", [])
    if not bf_rows:
        return result
    cur = result["rows"]
    index = {}
    for i, r in enumerate(cur):
        index.setdefault((P.norm(r["athlete"]), r["event"]), i)

    for bf in bf_rows:
        key = (P.norm(bf["athlete"]), bf["event"])
        row = {
            "athlete": bf["athlete"], "athlete_id": bf.get("athlete_id"),
            "tier": bf.get("tier"), "division": bf.get("division", ""),
            "event": bf["event"], "rig": bf.get("rig", ""),
            "wave": str(bf.get("wave", "")), "wave_start": bf.get("wave_start", ""),
            "run_order": str(bf.get("run_order", "")),
            "status": "completed",
            "sort_key": sort_key_from_wave_start(day_order, bf.get("wave_start", "")),
        }
        if key in index:
            if cur[index[key]].get("status") != "posted":
                cur[index[key]] = row
        else:
            index[key] = len(cur)
            cur.append(row)
    result["counts"]["rows"] = len(cur)
    return result


def fetch_previous(settings: dict, fetcher: Fetcher) -> dict | None:
    url = settings.get("published_data_url")
    if not url:
        return None
    try:
        text = fetcher.get(url)
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        log.info("No previous snapshot (%s)", exc)
        return None


# --- results / placement ----------------------------------------------------

def add_results_data(result: dict, settings: dict, fetcher: Fetcher,
                     prev: dict | None, tracked_ids: set[str]) -> dict:
    """Fetch results per (division, event), decide which are final, and record
    the tracked athletes' placements.

    A (tier, division, event) is finalized only when its results table is
    non-empty, its run order is empty, and the full results are unchanged since
    the previous scrape. Once final it is latched (frozen) via results_state, so
    transient mid-event placements never surface and a reappearing run order
    cannot un-finalize it.
    """
    base_url = settings["base_url"]
    results_pages = settings.get("results_pages", {})
    event_labels = settings.get("results_event_labels", {})
    prev_state = (prev or {}).get("results_state", {})
    run_remaining = result.get("_run_remaining", {})
    past_end = result.get("_past_end", {})

    # Discover the results embed + nav (division/event id maps) per tier in use.
    bases: dict[int, str] = {}
    navs: dict[int, dict] = {}
    for tier in sorted({r["tier"] for r in result["rows"] if r.get("tier")}):
        path = results_pages.get(str(tier))
        if not path:
            continue
        try:
            src = P.extract_embed_src(fetcher.get(urljoin(base_url, path)))
            if not src:
                continue
            bases[tier] = src
            navs[tier] = P.parse_results_nav(fetcher.get(src, referer=True))
            log.info("results tier %s: %s (%d divisions, %d events)", tier, src,
                     len(navs[tier]["divisions"]), len(navs[tier]["events"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("results discovery failed for tier %s: %s", tier, exc)

    combos = {(r["tier"], P.norm(r["division"]), r["event"]) for r in result["rows"]}
    state: dict[str, dict] = {}
    for tier, div_key, event in sorted(combos, key=lambda c: (c[0] or 0, c[1], c[2])):
        nav, base = navs.get(tier), bases.get(tier)
        if not nav or not base:
            continue
        lb = nav["divisions"].get(div_key)
        cat = nav["events"].get(P.norm(event_labels.get(event, "")))
        if not lb or not cat:
            continue
        key = f"{tier}|{div_key}|{event}"
        try:
            table = P.parse_results_table(
                fetcher.get(f"{base}?lb_gm_id={lb}&category={cat}", referer=True))
        except Exception as exc:  # noqa: BLE001
            log.warning("results fetch failed %s: %s", key, exc)
            continue
        all_places = {r.athlete_id: r.place for r in table if r.athlete_id}
        my_places = {i: p for i, p in all_places.items() if i in tracked_ids}
        digest = hashlib.md5(
            json.dumps(sorted(all_places.items())).encode()).hexdigest()[:12] if all_places else ""

        pst = prev_state.get(key, {})
        stable = bool(all_places) and digest == pst.get("hash")
        stable_count = (pst.get("stable_count", 0) + 1) if stable else 0
        # "Settled" = nobody is genuinely still queued: the run order is empty, or
        # everyone still listed has already finished (a "restored"/repopulated run
        # order). If anyone in the run order is absent from the results, the event
        # is still upcoming/running — don't finalize, even if the results table
        # happens to be unchanged (e.g. a partial Discipline Circuit Overall that
        # isn't moving). Re-evaluated each scrape, so it self-corrects.
        # Final once results are present AND the event is over: the run order is
        # empty (normal completion), or we're past its scheduled end time. The
        # scheduled-end path keeps multi-day DC and "restored" run orders correct —
        # a partial Discipline Circuit Overall stays in-progress until its last day's
        # runs are done, no matter how stable the standings look meanwhile.
        run_empty = run_remaining.get(key, 0) == 0
        final = (bool(all_places) and stable_count >= 1
                 and (run_empty or past_end.get(key, False)))
        state[key] = {
            "final": final, "in_progress": (not final and bool(all_places)),
            "started": bool(all_places), "hash": digest,
            "stable_count": stable_count, "places": my_places,
        }
        log.info("  results %-34s finishers=%-3d final=%s",
                 key, len(all_places), state[key]["final"])

    result["results_state"] = state
    return result


def assign_places(result: dict, qualifying: set[str]) -> None:
    """Set each row's ``place`` from its event's finalized results.

    Also reclassify rows still marked TBA only because the athlete was added after
    their event ran: if the event's results are final, a placed athlete becomes
    ``completed`` and one absent from a finalized qualifier becomes
    ``did_not_qualify`` (run-order-based detection can't see this post-event).
    """
    state = result.get("results_state", {})
    current_wave = result.get("_current_wave", {})
    for row in result["rows"]:
        key = f"{row.get('tier')}|{P.norm(row['division'])}|{row['event']}"
        st = state.get(key) or {}
        final = bool(st.get("final"))
        places = st.get("places", {})
        aid = row.get("athlete_id") or ""
        has_run = aid in places                 # athlete appears in the live results
        row["event_final"] = final
        row["event_in_progress"] = bool(st.get("in_progress"))
        row["has_run"] = has_run
        row["place"] = places.get(aid, "") if final else ""

        # Wave status is collective: the lowest wave still in the run order is the
        # one running; anything below it is done (every athlete in it has run);
        # anything above is upcoming. Independent of whether *our* athlete has run.
        try:
            wn = int(row.get("wave"))
        except (TypeError, ValueError):
            wn = None
        started = bool(st.get("started"))
        current = current_wave.get(key)           # lowest wave still queued, or None
        if final:
            row["wave_state"] = "over"            # whole event done -> wave done too
        elif wn is not None and current is not None:
            if wn < current:
                row["wave_state"] = "over"        # every athlete in this wave has run
            elif wn == current:
                row["wave_state"] = "in_progress" if started else "upcoming"
            else:
                row["wave_state"] = "upcoming"
        elif wn is not None:                       # run order empty for this event
            row["wave_state"] = "over" if started else "upcoming"
        else:
            row["wave_state"] = ""

        # Reclassify from the finalized results: a "posted" row whose run order has
        # repopulated post-event, or a "tba" row for an athlete added after their
        # event ran.
        if final and has_run and row["status"] == "posted":
            row["status"] = "completed"
        elif final and row["status"] == "tba":
            if row["place"]:
                row["status"] = "completed"
            elif row["event"] in qualifying:
                row["status"] = "did_not_qualify"
            else:
                row["status"] = "completed"


# --- head-to-head -----------------------------------------------------------

def add_h2h_rows(result: dict, settings: dict, fetcher: Fetcher,
                 athletes: list[dict], day_order: list[str]) -> None:
    """Add Head-to-Head rows — a track parallel to the tiers.

    H2H has one embed with a wave per division; the listed order is the H2H
    seeding (used as the run order). Tier is shown as "H2"; rig and wave are blank.
    """
    path = settings.get("h2h_page")
    if not path:
        return
    try:
        src = P.extract_embed_src(fetcher.get(urljoin(settings["base_url"], path)))
        if not src:
            return
        page = P.parse_embed(fetcher.get(src, referer=True))
    except Exception as exc:  # noqa: BLE001 — H2H is a bonus; never sink the run
        log.warning("H2H scrape failed: %s", exc)
        return

    by_id: dict[str, P.EmbedRow] = {}
    by_name: dict[str, P.EmbedRow] = {}
    for r in page.rows:
        if r.athlete_id:
            by_id.setdefault(r.athlete_id, r)
        by_name.setdefault(r.name_key, r)

    added = 0
    for spec in athletes:
        r = by_id.get(spec["id"]) if spec.get("id") else None
        if r is None:
            r = by_name.get(spec["name_key"])
        if r is None:
            continue
        result["rows"].append({
            "athlete": r.name, "athlete_id": r.athlete_id,
            "tier": "H2", "division": r.division, "event": "Head to Head",
            "rig": "", "wave": "", "wave_start": wave_start_str(r.weekday, r.time_disp),
            "run_order": str(r.position), "status": "posted",
            "sort_key": sort_key(day_order, r.weekday, r.time_min),
        })
        added += 1
        log.info("  H2H: %s (%s) seed %d %s",
                 r.name, r.division, r.position, wave_start_str(r.weekday, r.time_disp))
    result["counts"]["rows"] = len(result["rows"])


# --- main -------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scrape WNL run orders into site/data/.")
    ap.add_argument("--max-pages", type=int, default=None, help="limit rig pages (dev)")
    ap.add_argument("--no-persist", action="store_true", help="ignore previous snapshot")
    ap.add_argument("--prev-file", type=Path, default=None,
                    help="load the previous snapshot from a local file (dev/testing)")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output directory")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose or True else logging.WARNING,
        format="%(message)s",
    )

    settings = load_json(CONFIG_DIR / "settings.json")
    athletes = load_athletes(load_json(CONFIG_DIR / "athletes.json"))
    log.info("Tracking %d preset athlete(s): %s",
             len(athletes), ", ".join(a["name"] for a in athletes))

    fetcher = Fetcher(settings)
    try:
        result = build(settings, athletes, fetcher, max_pages=args.max_pages)
        tracked_ids = ({a["id"] for a in athletes if a.get("id")}
                       | {r["athlete_id"] for r in result["rows"] if r.get("athlete_id")})
        if args.prev_file and args.prev_file.exists():
            prev = load_json(args.prev_file)
        elif not args.no_persist:
            prev = fetch_previous(settings, fetcher)
        else:
            prev = None
        try:
            result = add_results_data(result, settings, fetcher, prev, tracked_ids)
        except Exception as exc:  # noqa: BLE001 — results are a bonus; never sink the run
            log.warning("results stage failed (continuing without placements): %s", exc)
            result.setdefault("results_state", {})
        add_h2h_rows(result, settings, fetcher, athletes, settings.get("event_day_order", []))
    finally:
        fetcher.close()

    result = merge_previous(result, prev)

    bf_path = CONFIG_DIR / "backfill.json"
    backfill = load_json(bf_path) if bf_path.exists() else None
    result = apply_backfill(result, backfill, settings.get("event_day_order", []))

    assign_places(result, set(settings.get("qualifying_events", ["Stage 2", "Stage 3"])))
    result.pop("_run_remaining", None)
    result.pop("_current_wave", None)
    result.pop("_past_end", None)
    result["rows"].sort(key=lambda r: (P.norm(r["athlete"]), r["sort_key"]))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "athletes.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "meta.json").write_text(
        json.dumps({"generated_at": result["generated_at"], "counts": result["counts"]},
                   indent=2), encoding="utf-8")

    log.info("Wrote %d rows for %d athlete(s) -> %s",
             result["counts"]["rows"], result["counts"]["matched_athletes"], args.out)
    for u in result["unmatched"]:
        hint = f"  (did you mean: {', '.join(u['suggestions'])}?)" if u["suggestions"] else ""
        log.warning("Unmatched: %s%s", u["name"], hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
