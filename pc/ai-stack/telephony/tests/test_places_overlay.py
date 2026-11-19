"""
The admin overlay: places added from a browser, layered on the researched YAML.

WHAT THESE TESTS ARE ACTUALLY PROTECTING
The overlay is a file written by one container and read by another while a call
is in progress. Three ways that goes wrong, and one test each:

  1. the overlay quietly REPLACES the researched directory instead of adding to
     it -- forty verified places become one, and nobody notices until a caller
     asks for a pharmacy
  2. a broken or half-written overlay takes the whole directory down with it,
     mid-call
  3. an entry with no category lands in `parks`, where the summer rules hide it
     for five months of the year

None of those raise. All of them are silent. That is why they are tested.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.places import DEFAULT_OVERLAY_SECTION, Places

CONFIG = Path(__file__).resolve().parents[2] / "config" / "places.yaml"


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def loaded(tmp_path: Path, payload) -> Places:
    """A directory with `payload` as its overlay."""
    overlay = tmp_path / "places.json"
    write(overlay, payload)
    return Places(CONFIG, overlay=overlay)


def names_in(p: Places, section: str) -> list[str]:
    return [e.get("name") for e in p.data.get(section, [])]


# --- adding ------------------------------------------------------------------

def test_an_added_place_joins_the_researched_ones_rather_than_replacing_them(tmp_path):
    base = Places(CONFIG, overlay=tmp_path / "nothing.json")
    p = loaded(tmp_path, {"places": [
        {"name": "Walnut Recreation Center", "category": "senior_centers",
         "address": "3075 N. Walnut Rd."},
    ]})

    assert p.count() == base.count() + 1
    assert p.overlay_count() == 1
    assert "Walnut Recreation Center" in names_in(p, "senior_centers")
    # And the researched entries are all still there.
    for name in names_in(base, "senior_centers"):
        assert name in names_in(p, "senior_centers")


def test_a_bare_list_is_accepted_as_well_as_a_places_object(tmp_path):
    p = loaded(tmp_path, [{"name": "Somewhere", "category": "libraries"}])
    assert "Somewhere" in names_in(p, "libraries")


def test_an_added_place_is_findable_by_search(tmp_path):
    # Searched for by a word in its own name rather than by a tag. Tags tie with
    # the two dozen researched entries that share them, and this test is about
    # "the entry reached the index at all", not about ranking.
    p = loaded(tmp_path, {"places": [
        {"name": "Vandenberg Reading Room", "category": "indoor_summer",
         "good_for": ["quiet", "shade"], "address": "1600 N Rampart"},
    ]})
    found = [e["name"] for e in p.search("vandenberg", limit=5)]
    assert "Vandenberg Reading Room" in found


def test_an_entry_that_names_no_category_does_not_land_outdoors(tmp_path):
    # Landing in `parks` would mean the summer rules bury it from May to
    # September, which looks exactly like the entry never saved.
    p = loaded(tmp_path, {"places": [{"name": "No Category Here"}]})
    assert "No Category Here" in names_in(p, DEFAULT_OVERLAY_SECTION)
    assert "No Category Here" not in names_in(p, "parks")


def test_a_spoken_category_is_understood_not_turned_into_a_new_section(tmp_path):
    # "pharmacy", not "pharmacies". Left alone it would create a section of one
    # that no alias points at, so the entry would be unreachable by category.
    p = loaded(tmp_path, {"places": [
        {"name": "The Corner Chemist", "category": "pharmacy"},
    ]})
    assert "The Corner Chemist" in names_in(p, "pharmacies")
    assert "pharmacy" not in p.sections()


def test_an_added_place_carries_a_source_even_when_none_was_given(tmp_path):
    # Every entry in the YAML cites where it came from, and the test that
    # enforces that would otherwise be quietly bypassed by the admin.
    p = loaded(tmp_path, {"places": [{"name": "Unsourced", "category": "libraries"}]})
    entry = next(e for e in p.data["libraries"] if e["name"] == "Unsourced")
    assert entry["source"]


# --- correcting --------------------------------------------------------------

def test_the_same_name_corrects_the_researched_entry_instead_of_duplicating_it(tmp_path):
    base = Places(CONFIG, overlay=tmp_path / "nothing.json")
    existing = base.data["senior_centers"][0]["name"]

    p = loaded(tmp_path, {"places": [
        {"name": existing, "category": "senior_centers", "phone": "555-0166"},
    ]})

    assert names_in(p, "senior_centers").count(existing) == 1
    corrected = next(e for e in p.data["senior_centers"] if e["name"] == existing)
    assert corrected["phone"] == "555-0166"
    assert p.count() == base.count(), "correcting is not adding"


# --- surviving a bad file ----------------------------------------------------

@pytest.mark.parametrize("body", [
    "{ this is not json",
    '"a bare string"',
    '{"places": "not a list"}',
])
def test_a_broken_overlay_is_ignored_and_the_directory_still_works(tmp_path, body):
    # This file can be read mid-write. It must never be able to take the phone
    # line's directory with it.
    overlay = tmp_path / "places.json"
    overlay.write_text(body, encoding="utf-8")
    p = Places(CONFIG, overlay=overlay)

    assert p.count() > 40
    assert p.search("pharmacy", limit=3)


def test_a_missing_overlay_is_a_normal_state_not_an_error(tmp_path):
    p = Places(CONFIG, overlay=tmp_path / "never-written.json")
    assert p.count() > 40
    assert p.overlay_count() == 0


def test_entries_without_a_name_are_skipped_rather_than_listed_blank(tmp_path):
    p = loaded(tmp_path, {"places": [
        {"category": "libraries", "address": "nowhere"},
        {"name": "Real One", "category": "libraries"},
    ]})
    assert p.overlay_count() == 1
    assert None not in names_in(p, "libraries")


# --- picking up a change without a restart -----------------------------------

def test_a_place_added_after_startup_is_visible_without_a_restart(tmp_path):
    overlay = tmp_path / "places.json"
    write(overlay, {"places": []})
    p = Places(CONFIG, overlay=overlay)
    assert "Added Later" not in names_in(p, "libraries")

    write(overlay, {"places": [{"name": "Added Later", "category": "libraries"}]})
    p._checked_at = 0.0        # skip the poll interval; the point is the reload
    p.refresh()

    assert "Added Later" in names_in(p, "libraries")


def test_removing_a_place_from_the_overlay_really_removes_it(tmp_path):
    # The merge builds a fresh copy each time rather than mutating in place. If
    # it ever stops doing that, this is the test that says so: a removed entry
    # would linger in memory until the container restarted.
    overlay = tmp_path / "places.json"
    write(overlay, {"places": [{"name": "Temporary", "category": "libraries"}]})
    p = Places(CONFIG, overlay=overlay)
    assert "Temporary" in names_in(p, "libraries")

    write(overlay, {"places": []})
    p._checked_at = 0.0
    p.refresh()

    assert "Temporary" not in names_in(p, "libraries")
    assert p.count() > 40, "and the researched base survived the round trip"


def test_the_researched_base_is_never_mutated_by_a_merge(tmp_path):
    overlay = tmp_path / "places.json"
    write(overlay, {"places": [{"name": "Temporary", "category": "libraries"}]})
    p = Places(CONFIG, overlay=overlay)

    assert "Temporary" not in [e.get("name") for e in p.base["libraries"]]
