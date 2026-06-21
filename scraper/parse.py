"""Pure HTML -> data parsing for Ninja Times.

Everything here is side-effect free (no network, no filesystem) so it can be unit
tested against saved HTML fixtures. ``scrape.py`` handles I/O and orchestration.

The two HTML shapes we parse:

1. The WNL master schedule table on ``/tier-1-championships/`` (covers both tiers):
   columns ``Division | Tier | Event | Day | Time | Rig Assignment | Run Order(link)``.

2. The ``timing.ninjaworks.com`` embed for one rig: a sequence of
   ``<h3>Wave: T1 Mature Kids Male 8 Saturday 3:53PM</h3>`` headings, each followed by a
   ``<table>`` whose rows are ``Name (id) | Division | Coach`` and whose run-order position
   is the row order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# --- regexes & small lookups ------------------------------------------------

_WEEKDAY_FULL = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]
_WEEKDAY_ABBR = {d: d[:3] for d in _WEEKDAY_FULL}
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAY_FULL) + r")\b", re.I)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])")
_TIER_RE = re.compile(r"\bT(?:ier)?\s*([12])\b", re.I)
_ATHLETE_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")
_LEADING_TIER_RE = re.compile(r"^T([12])\b\s*", re.I)

TESTER_NAMES = {"course tester", "test athlete", "tester", "forerunner"}


def norm(s: str | None) -> str:
    """Lowercase + collapse whitespace, for case-insensitive matching."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def clean(s: str | None) -> str:
    """Collapse whitespace but preserve original casing (for display)."""
    return re.sub(r"\s+", " ", (s or "").strip())


# --- primitive field parsers ------------------------------------------------

def parse_clock(text: str | None) -> tuple[str, int] | None:
    """First clock time in *text* -> ("3:53 PM", minutes_since_midnight)."""
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour, minute, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    disp = f"{hour}:{minute:02d} {ap}"
    hour24 = (hour % 12) + (12 if ap == "PM" else 0)
    return disp, hour24 * 60 + minute


def parse_weekday(text: str | None) -> str | None:
    """First weekday in *text* -> canonical full name ("Saturday")."""
    m = _WEEKDAY_RE.search(text or "")
    if not m:
        return None
    return m.group(1).title()


def weekday_abbr(full: str | None) -> str:
    return _WEEKDAY_ABBR.get((full or "").title(), "")


def normalize_tier(text: str | None) -> int | None:
    m = _TIER_RE.search(text or "")
    return int(m.group(1)) if m else None


def normalize_event(text: str | None) -> str | None:
    """Map a free-text event label to a canonical token."""
    t = norm(text)
    if not t:
        return None
    if "discipline" in t or t == "dc" or t.startswith("dc "):
        return "DC"
    if "head" in t and "head" in t.replace("-", " "):  # head-to-head / head to head
        return "H2H"
    if t in {"h2h"}:
        return "H2H"
    m = re.search(r"stage\s*([123])", t)
    if m:
        return f"Stage {m.group(1)}"
    return clean(text)  # unknown — keep as-is rather than dropping


# --- rig slug -> (tier, event, rig label) -----------------------------------

_SLUG_RE = re.compile(r"tier-([12])-(stage-[123]|dc|h2h)-(small|tall-a|tall-b|tall)\b")
_RIG_LABEL = {"small": "Small", "tall": "Tall", "tall-a": "Tall A", "tall-b": "Tall B"}


def parse_rig_slug(slug: str) -> dict | None:
    """'tier-1-stage-1-tall-a' -> {tier:1, event:'Stage 1', rig:'Stage 1 Tall A'}."""
    slug = (slug or "").strip().strip("/").rsplit("/", 1)[-1].lower()
    if slug == "h2h":
        return {"tier": None, "event": "H2H", "rig": "Head-to-Head", "slug": slug}
    m = _SLUG_RE.search(slug)
    if not m:
        return None
    tier = int(m.group(1))
    ev_raw, rig_raw = m.group(2), m.group(3)
    if ev_raw.startswith("stage-"):
        event = f"Stage {ev_raw.split('-')[1]}"
    elif ev_raw == "dc":
        event = "DC"
    else:
        event = "H2H"
    rig = f"{event} {_RIG_LABEL.get(rig_raw, rig_raw.title())}"
    return {"tier": tier, "event": event, "rig": rig, "slug": slug}


# --- athlete name cell ------------------------------------------------------

def parse_athlete(cell: str | None) -> tuple[str, str | None]:
    """'Logan Kiselica (235867)' -> ('Logan Kiselica', '235867')."""
    text = clean(cell)
    m = _ATHLETE_RE.match(text)
    if m:
        return clean(m.group(1)), m.group(2)
    return text, None


def is_tester(name: str | None) -> bool:
    return norm(name) in TESTER_NAMES


# --- wave heading -----------------------------------------------------------

@dataclass
class WaveInfo:
    tier: int | None
    division: str
    division_key: str
    wave: int | None
    weekday: str | None       # full name, e.g. "Saturday"
    time_disp: str | None     # "3:53 PM"
    time_min: int | None
    raw: str


def parse_wave_heading(text: str) -> WaveInfo:
    """Parse 'Wave: T1 Mature Kids Male 8 Saturday 3:53PM' (and DC variants).

    Format: ``Wave: T{tier} {division words} [DC] {wave#} [{Weekday} {Time}]``.
    Day/time are sometimes absent; division is multi-word; the trailing integer
    before the weekday is the wave number.
    """
    raw = clean(text)
    s = re.sub(r"^\s*wave\s*:?\s*", "", raw, flags=re.I)

    tier = None
    mt = _LEADING_TIER_RE.match(s)
    if mt:
        tier = int(mt.group(1))
        s = s[mt.end():]

    # Pull day/time off the tail.
    clock = parse_clock(s)
    time_disp, time_min = (clock if clock else (None, None))
    weekday = parse_weekday(s)

    head = s
    if weekday:
        # Everything from the weekday onward is the day/time tail.
        idx = _WEEKDAY_RE.search(s)
        if idx:
            head = s[: idx.start()]
    head = clean(head)

    # head is now like "Mature Kids Male 8" or "Kids Male DC 1".
    tokens = head.split()
    wave = None
    if tokens and re.fullmatch(r"\d+", tokens[-1]):
        wave = int(tokens[-1])
        tokens = tokens[:-1]
    if tokens and tokens[-1].upper() == "DC":
        tokens = tokens[:-1]
    division = clean(" ".join(tokens))

    return WaveInfo(
        tier=tier,
        division=division,
        division_key=norm(division),
        wave=wave,
        weekday=weekday,
        time_disp=time_disp,
        time_min=time_min,
        raw=raw,
    )


# --- schedule table ---------------------------------------------------------

@dataclass
class ScheduleEntry:
    tier: int | None
    division: str
    division_key: str
    event: str | None
    weekday: str | None
    start_disp: str | None
    start_min: int | None
    rig: str
    slug: str | None
    url: str | None


def _table_headers(table) -> list[str]:
    head = table.find("tr")
    if not head:
        return []
    return [norm(c.get_text()) for c in head.find_all(["th", "td"])]


def parse_schedule_table(html: str, base_url: str = "") -> list[ScheduleEntry]:
    """Parse the WNL master schedule table into structured entries."""
    soup = BeautifulSoup(html, "lxml")
    target = None
    for table in soup.find_all("table"):
        headers = _table_headers(table)
        if "division" in headers and ("run order" in headers or "rig assignment" in headers):
            target = table
            break
    if target is None:
        return []

    headers = _table_headers(target)
    col = {name: i for i, name in enumerate(headers)}

    def cell(cells, name):
        i = col.get(name)
        return cells[i] if i is not None and i < len(cells) else None

    entries: list[ScheduleEntry] = []
    rows = target.find_all("tr")
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells or len(cells) < 3:
            continue
        div_txt = clean(cell(cells, "division").get_text()) if cell(cells, "division") else ""
        if not div_txt:
            continue
        tier = normalize_tier(cell(cells, "tier").get_text() if cell(cells, "tier") else "")
        event = normalize_event(cell(cells, "event").get_text() if cell(cells, "event") else "")
        weekday = parse_weekday(cell(cells, "day").get_text() if cell(cells, "day") else "")
        time_cell = cell(cells, "time")
        clk = parse_clock(time_cell.get_text() if time_cell else "")
        start_disp, start_min = (clk if clk else (None, None))
        rig_cell = cell(cells, "rig assignment")
        rig = clean(rig_cell.get_text()) if rig_cell else ""
        # run-order link (last cell usually holds the <a>)
        link = tr.find("a", href=True)
        url = link["href"] if link else None
        slug = None
        if url:
            slug = url.strip("/").rsplit("/", 1)[-1].lower()
        entries.append(ScheduleEntry(
            tier=tier, division=div_txt, division_key=norm(div_txt), event=event,
            weekday=weekday, start_disp=start_disp, start_min=start_min,
            rig=rig, slug=slug, url=url,
        ))
    return entries


# --- ninjaworks embed -------------------------------------------------------

def extract_embed_src(html: str) -> str | None:
    """Find the ninjaworks (or any) iframe src on a WNL rig page."""
    soup = BeautifulSoup(html, "lxml")
    iframes = soup.find_all("iframe", src=True)
    for f in iframes:
        if "ninjaworks.com" in f["src"] or "/embed/" in f["src"]:
            return f["src"]
    return iframes[0]["src"] if iframes else None


@dataclass
class EmbedRow:
    name: str
    name_key: str
    athlete_id: str | None
    tier: int | None
    division: str
    division_key: str
    wave: int | None
    weekday: str | None
    time_disp: str | None
    time_min: int | None
    position: int          # run-order position within the wave (excludes tester rows)


@dataclass
class EmbedPage:
    title: str
    rows: list[EmbedRow] = field(default_factory=list)
    divisions_present: set[str] = field(default_factory=set)  # division_keys
    wave_count: int = 0


def _iter_heading_tables(soup):
    """Yield (h3, table) pairs where the table directly follows the heading."""
    for h3 in soup.find_all("h3"):
        nxt = h3.find_next(["h3", "table"])
        if nxt is not None and nxt.name == "table":
            yield h3, nxt


def parse_embed(html: str, count_tester_in_position: bool = False) -> EmbedPage:
    """Parse a ninjaworks rig embed into per-athlete rows.

    ``position`` is the athlete's 1-based run order within the wave. By default the
    ``Course Tester`` placeholder row is excluded from both the output and the numbering.
    """
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    page = EmbedPage(title=clean(h1.get_text()) if h1 else "")

    for h3, table in _iter_heading_tables(soup):
        wave = parse_wave_heading(h3.get_text())
        position = 0
        trs = table.find_all("tr")
        for tr in trs[1:]:  # skip header row
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            name, athlete_id = parse_athlete(cells[0].get_text())
            if not name:
                continue
            tester = is_tester(name)
            if tester and not count_tester_in_position:
                continue  # excluded entirely
            position += 1
            if tester:
                continue  # counted (if configured) but not emitted as an athlete
            div_cell = clean(cells[1].get_text()) if len(cells) > 1 else ""
            division = div_cell or wave.division
            division_key = norm(division)
            page.divisions_present.add(division_key)
            page.rows.append(EmbedRow(
                name=name, name_key=norm(name), athlete_id=athlete_id,
                tier=wave.tier, division=division, division_key=division_key,
                wave=wave.wave, weekday=wave.weekday,
                time_disp=wave.time_disp, time_min=wave.time_min,
                position=position,
            ))
        page.wave_count += 1
    return page


# --- results embed ----------------------------------------------------------

_LBGM_RE = re.compile(r"lb_gm_id=(\d+)")
_CAT_RE = re.compile(r"category=(\d+)")


def parse_results_nav(html: str) -> dict:
    """Parse a ninjaworks results embed's nav into id maps.

    The results widget is one embed per tier whose internal tabs select a
    division (``lb_gm_id`` in the nav-pills) and an event (``category`` in the
    nav-tabs). The nav lists every division and event regardless of current
    selection, so one fetch yields the whole mapping.
    """
    soup = BeautifulSoup(html, "lxml")
    divisions: dict[str, str] = {}          # division_key -> lb_gm_id
    events: dict[str, str] = {}             # event_label_key -> category

    pills = soup.select_one("ul.nav-pills")
    if pills:
        for a in pills.find_all("a", href=True):
            name = clean(a.get_text())
            m = _LBGM_RE.search(a["href"])
            if name and m:
                divisions[norm(name)] = m.group(1)

    tabs = soup.select_one("ul.nav-tabs")
    if tabs:
        for a in tabs.find_all("a", href=True):
            label = clean(a.get_text())
            m = _CAT_RE.search(a["href"])
            if label and m:
                events[norm(label)] = m.group(1)

    return {"divisions": divisions, "events": events}


@dataclass
class ResultRow:
    place: str
    name: str
    name_key: str
    athlete_id: str | None
    result: str


def parse_results_table(html: str) -> list[ResultRow]:
    """Parse a results table: rows are ``# | Name | Result | …`` each followed by
    a collapsed row reading ``Name is NinjaWorks Athlete ID NNNNN``."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    out: list[ResultRow] = []
    if not table:
        return out
    cur: ResultRow | None = None
    for tr in table.find_all("tr"):
        tds = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        full = " ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"]))
        m = re.search(r"is NinjaWorks Athlete ID\s*(\d+)", full)
        if m and cur is not None:
            cur.athlete_id = m.group(1)
            out.append(cur)
            cur = None
        elif tds and re.fullmatch(r"\d+", tds[0].strip()):
            name = clean(tds[1]) if len(tds) > 1 else ""
            cur = ResultRow(place=tds[0].strip(), name=name, name_key=norm(name),
                            athlete_id=None, result=clean(tds[2]) if len(tds) > 2 else "")
    return out
