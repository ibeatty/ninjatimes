"""Unit tests for the pure parsing layer.

These encode facts validated against live WNL data on 2026-06-20 (e.g. run-order
position excludes the Course Tester row; multi-word divisions; DC headings carry a
'DC' token; some headings lack day/time).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parse as P  # noqa: E402


# --- clocks -----------------------------------------------------------------

def test_parse_clock_basic():
    assert P.parse_clock("3:53PM") == ("3:53 PM", 15 * 60 + 53)
    assert P.parse_clock("7:30AM") == ("7:30 AM", 7 * 60 + 30)


def test_parse_clock_noon_and_midnight():
    assert P.parse_clock("12:00PM")[1] == 12 * 60      # noon
    assert P.parse_clock("12:00AM")[1] == 0            # midnight
    assert P.parse_clock("no time here") is None


# --- athlete cell -----------------------------------------------------------

def test_parse_athlete_with_id():
    assert P.parse_athlete("Logan Kiselica (235867)") == ("Logan Kiselica", "235867")


def test_parse_athlete_without_id():
    assert P.parse_athlete("Jane Doe") == ("Jane Doe", None)


def test_tester_detection():
    assert P.is_tester("Course Tester")
    assert P.is_tester("course tester")
    assert not P.is_tester("Logan Kiselica")


# --- wave headings ----------------------------------------------------------

def test_wave_heading_stage():
    w = P.parse_wave_heading("Wave: T1 Mature Kids Male 8 Saturday 3:53PM")
    assert w.tier == 1
    assert w.division == "Mature Kids Male"
    assert w.wave == 8
    assert w.weekday == "Saturday"
    assert w.time_disp == "3:53 PM"


def test_wave_heading_dc_token_stripped():
    w = P.parse_wave_heading("Wave: T2 Kids Male DC 1 Thursday 7:30AM")
    assert w.tier == 2
    assert w.division == "Kids Male"   # 'DC' token removed
    assert w.wave == 1
    assert w.weekday == "Thursday"


def test_wave_heading_missing_day_time():
    w = P.parse_wave_heading("Wave: T2 Preteen Male 3")
    assert w.division == "Preteen Male"
    assert w.wave == 3
    assert w.weekday is None
    assert w.time_disp is None


# --- rig slugs --------------------------------------------------------------

def test_rig_slug_variants():
    assert P.parse_rig_slug("tier-1-stage-1-tall-a") == {
        "tier": 1, "event": "Stage 1", "rig": "Stage 1 Tall A", "slug": "tier-1-stage-1-tall-a"}
    assert P.parse_rig_slug("tier-2-dc-small")["event"] == "DC"
    assert P.parse_rig_slug("tier-2-dc-small")["rig"] == "DC Small"
    assert P.parse_rig_slug("tier-1-stage-3-tall")["rig"] == "Stage 3 Tall"


def test_normalize_event_and_tier():
    assert P.normalize_event("Discipline Circuit") == "DC"
    assert P.normalize_event("Stage 2") == "Stage 2"
    assert P.normalize_tier("Tier 1") == 1
    assert P.normalize_tier("T2") == 2


# --- embed parsing: position excludes the tester row ------------------------

EMBED = """
<html><body>
<h1>Stage 1 Small Run Order</h1>
<h3>Wave: T1 Mature Kids Male 8 Saturday 3:53PM</h3>
<table>
  <tr><th>Name</th><th>Division</th><th>Course Coach</th></tr>
  <tr><td>Course Tester (282012)</td><td>Mature Kids Male</td><td></td></tr>
  <tr><td>Anna First (1)</td><td>Mature Kids Male</td><td>Coach A</td></tr>
  <tr><td>Bob Second (2)</td><td>Mature Kids Male</td><td>Coach B</td></tr>
</table>
<h3>Wave: T1 Preteen Male 1 Sunday 8:00AM</h3>
<table>
  <tr><th>Name</th><th>Division</th><th>Course Coach</th></tr>
  <tr><td>Cara Third (3)</td><td>Preteen Male</td><td></td></tr>
</table>
</body></html>
"""


def test_embed_positions_exclude_tester():
    page = P.parse_embed(EMBED)
    assert page.title == "Stage 1 Small Run Order"
    assert page.wave_count == 2
    # tester excluded; real athletes numbered from 1
    first_wave = [r for r in page.rows if r.wave == 8]
    names = {(r.name, r.position) for r in first_wave}
    assert names == {("Anna First", 1), ("Bob Second", 2)}
    assert all(not P.is_tester(r.name) for r in page.rows)


def test_embed_division_from_row():
    page = P.parse_embed(EMBED)
    assert "preteen male" in page.divisions_present
    assert "mature kids male" in page.divisions_present
