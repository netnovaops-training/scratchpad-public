"""
Places directory tests.

Two things are being protected here:
  1. the data is real -- every entry has a source, nothing was invented
  2. the seasonal logic works, because suggesting a park at 2pm in July is
     worse than suggesting nothing
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.places import Places, format_for_speech

CONFIG = Path(__file__).resolve().parents[2] / "config" / "places.yaml"


@pytest.fixture(scope="module")
def places():
    return Places(CONFIG)


# --- the data itself ---------------------------------------------------------

def test_file_loads_and_has_content(places):
    assert places.count() > 40
    assert "senior_centers" in places.sections()


def test_every_entry_cites_a_source(places):
    """No source means someone guessed. That is the failure mode that hurts."""
    missing = [e["name"] for e in places.all_entries() if not e.get("source")]
    assert not missing, f"no source: {missing}"


def test_every_place_has_an_address_or_is_a_phone_service(places):
    phone_only = {"helplines", "transport"}
    missing = [e["name"] for e in places.all_entries()
               if e["category"] not in phone_only and not e.get("address")]
    assert not missing, f"no address: {missing}"


def test_phone_numbers_look_like_phone_numbers(places):
    import re
    bad = [e["name"] for e in places.all_entries()
           if e.get("phone") and not re.match(r"^\(\d{3}\) \d{3}-\d{4}$", str(e["phone"]))]
    assert not bad, f"malformed phone: {bad}"


def test_warnings_survived(places):
    """These are the traps. Losing them silently would be bad."""
    w = " ".join(places.warnings()).lower()
    assert "derfelt" in w, "the repurposed-senior-centre warning is missing"
    assert "drop-in" in w


# --- searching ---------------------------------------------------------------

def test_finds_a_pharmacy(places):
    out = places.search("pharmacy", limit=3)
    assert out
    assert all(e["category"] == "pharmacies" for e in out)


def test_finds_dialysis_by_name(places):
    out = places.search("dialysis")
    assert any("DaVita" in e["name"] or "Dialysis" in e["name"] for e in out)


def test_category_aliases(places):
    assert places.resolve_category("somewhere with books") == "libraries"
    assert places.resolve_category("a senior center") == "senior_centers"
    assert places.resolve_category("prescription") == "pharmacies"
    assert places.resolve_category("nonsense words here") is None


# --- the seasonal logic, which is the useful bit -----------------------------

def test_july_afternoon_does_not_suggest_a_park(places):
    """110F at 2pm. Parks are not an answer."""
    out = places.suggest_outing("somewhere nice", when=datetime(2026, 7, 15, 14, 0))
    assert out
    assert all(e["category"] != "parks" for e in out), \
        [f"{e['name']} ({e['category']})" for e in out]


def test_july_early_morning_allows_shaded_parks(places):
    out = places.suggest_outing("shade outside", when=datetime(2026, 7, 15, 7, 30), limit=6)
    assert any(e["category"] == "parks" for e in out)


def test_january_afternoon_allows_parks(places):
    out = places.suggest_outing("outside in the fresh air",
                                when=datetime(2026, 1, 15, 14, 0), limit=6)
    assert any(e["category"] == "parks" for e in out)


def test_hot_weather_advice_is_attached(places):
    out = places.suggest_outing("park", when=datetime(2026, 8, 1, 15, 0))
    assert any("6 and 9" in e.get("_advice", "") for e in out)


def test_low_mobility_prefers_accessible_places(places):
    out = places.suggest_outing("somewhere to sit", when=datetime(2026, 8, 1, 15, 0),
                                low_mobility=True, limit=3)
    names = [e["name"] for e in out]
    tags = " ".join(t for e in out for t in e.get("good_for", []))
    assert any(g in tags for g in
               ("free_wheelchairs", "no_stairs", "accessible_parking",
                "fully_accessible", "seating", "flat_paths")), names


def test_quiet_request_finds_quiet_places(places):
    out = places.suggest_outing("somewhere quiet, she doesn't like noise",
                                when=datetime(2026, 3, 1, 11, 0), limit=3)
    tags = " ".join(t for e in out for t in e.get("good_for", []))
    assert "quiet" in tags


# --- speech ------------------------------------------------------------------

def test_speech_is_short_and_named(places):
    out = places.suggest_outing("cards", when=datetime(2026, 3, 1, 11, 0))
    said = format_for_speech(out)
    assert said
    assert said.count(";") <= 2, "never read more than three options down a phone"
    assert "89030" not in said, "don't read zip codes aloud"


def test_speech_handles_nothing_found():
    assert "don't have" in format_for_speech([])


# --- failure modes -----------------------------------------------------------

def test_missing_file_is_not_fatal(tmp_path):
    """The phone line must still answer calls without a directory."""
    p = Places(tmp_path / "nope.yaml")
    assert p.count() == 0
    assert p.search("pharmacy") == []
    assert p.suggest_outing("anywhere") == []


def test_broken_yaml_is_not_fatal(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("parks:\n  - name: x\n   bad indent: [\n")
    p = Places(bad)
    assert p.count() == 0


def test_drive_through_request_prefers_confirmed_ones(places):
    """
    Several pharmacies are marked drive_through: unconfirmed. Offering one of
    those to someone who cannot get out of the car is worse than offering
    nothing -- they drive there and find they have to go inside.
    """
    out = places.search("pharmacy with a drive through", limit=3)
    assert out
    # YAML reads a bare `yes` as the boolean True, so accept both forms --
    # asserting only on the string is what let this bug hide in the first place.
    def confirmed(e):
        dt = e.get("drive_through", "")
        return dt is True or str(dt).strip().lower() in ("yes", "true")
    assert confirmed(out[0]), \
        f"led with {out[0]['name']} (drive_through={out[0].get('drive_through')!r})"
    assert all(confirmed(e) for e in out), \
        "an unconfirmed drive-through made it into a drive-through search"


def test_naming_a_category_never_returns_nothing(places):
    """
    The staleness penalty once dragged every pharmacy to zero and "pharmacy"
    returned an empty list. Score should ORDER a matched category, never
    empty it.
    """
    for q in ("pharmacy", "library", "park", "dialysis", "hospice"):
        assert places.search(q, limit=3), f"{q!r} returned nothing"
