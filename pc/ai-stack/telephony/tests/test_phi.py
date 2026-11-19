"""
Health-information tests.

The cases that matter are drawn from how families of very unwell people
actually talk on the phone -- unprompted, mid-sentence, while arranging a lift
to the shops. Those are the sentences that have to not end up in a database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.phi import (
    REDACTED,
    CallRecord,
    clean_record,
    from_extraction,
    generalise_destination,
    mentions_health,
    scrub,
)

# --- the allowlist is the real protection ------------------------------------

def test_unknown_fields_are_discarded_entirely():
    """
    The central guarantee. If the model returns a medical field -- because a
    caller volunteered it and the model was helpful -- there must be nowhere
    for it to land.
    """
    rec = from_extraction({
        "caller_name": "Linda",
        "client_name": "Mrs Alvarez",
        "pickup_address": "4500 Rancho",
        # everything below is invented by the model and must vanish
        "medical_history": "stage 4 lung cancer, on morphine",
        "diagnosis": "dementia",
        "medications": "oxycodone 10mg",
        "notes": "she's declining fast, hospice says weeks",
        "health_status": "terminal",
    })
    d = rec.to_dict()
    assert d["caller_name"] == "Linda"
    for gone in ("medical_history", "diagnosis", "medications", "notes", "health_status"):
        assert gone not in d

    blob = " ".join(str(v) for v in d.values()).lower()
    for leak in ("cancer", "morphine", "dementia", "oxycodone", "hospice", "terminal"):
        assert leak not in blob, f"{leak} leaked into the record"


def test_record_has_no_freeform_bucket():
    """
    A 'notes' or 'other' field would quietly undo the whole design -- it
    becomes where everything goes.
    """
    fields = set(CallRecord.__dataclass_fields__)
    for banned in ("notes", "other", "comments", "details", "summary", "transcript", "extra"):
        assert banned not in fields


# --- scrubbing: the second line ----------------------------------------------

@pytest.mark.parametrize("said", [
    "she has dementia",
    "he's on morphine now",
    "mum had a stroke last year",
    "she's in hospice care",
    "he's on oxycodone and gabapentin",
    "she's diabetic",
    "he goes for dialysis on Tuesdays",
    "her oncologist wants to see her",
    "he has a DNR",
    "she's on lisinopril",
    "stage 4 cancer",
    "he's terminal",
])
def test_health_terms_are_caught(said):
    assert mentions_health(said), said
    assert REDACTED in scrub(said), said


@pytest.mark.parametrize("said", [
    "she likes to go to the garden centre",
    "pick her up at half past two",
    "she lives at 4500 Rancho Drive apartment 12",
    "her daughter Linda is the one to call",
    "she uses a walker",
    "about three hours should do it",
])
def test_ordinary_details_survive_untouched(said):
    """Over-redaction makes the record useless. These must come through clean."""
    assert not mentions_health(said), said
    assert scrub(said) == said, said


# --- destinations: keep where, drop why --------------------------------------

def test_medical_destination_keeps_address_drops_specialty():
    out = generalise_destination("the dialysis centre at 4500 Rancho")
    assert "4500 Rancho" in out
    assert "dialysis" not in out.lower()
    assert "medical appointment" in out.lower()


def test_pharmacy_stays_a_pharmacy():
    """Collecting a prescription IS the service. It's an errand, not a diagnosis."""
    out = generalise_destination("CVS pharmacy on Craig Road")
    assert "pharmacy" in out.lower()
    assert "Craig Road" in out


def test_errands_are_untouched():
    for place in ("Smith's grocery on Craig", "the bank", "church on Sunday",
                  "lunch at the diner", "the library"):
        assert generalise_destination(place) == place, place


def test_specialty_elsewhere_in_the_string_is_still_caught():
    out = generalise_destination("the clinic on Rancho for her cancer treatment")
    assert "cancer" not in out.lower()
    assert "Rancho" in out


# --- the whole record --------------------------------------------------------

def test_clean_record_scrubs_every_free_text_field():
    rec = CallRecord(
        caller_name="Linda (daughter, she has dementia)",
        client_name="Mrs Alvarez",
        destination="oncology at 900 Shadow Lane",
        pickup_address="4500 Rancho, she's on oxygen",
        when="Tuesday at 2",
    )
    out = clean_record(rec)
    blob = " ".join(out.to_dict().values() if False else
                    [str(v) for v in out.to_dict().values()]).lower()
    for leak in ("dementia", "oncology", "oxygen"):
        assert leak not in blob, leak
    assert "900 Shadow Lane" in out.destination
    assert "4500 Rancho" in out.pickup_address
    assert out.when == "Tuesday at 2"


def test_mobility_aid_keeps_practical_drops_clinical():
    assert clean_record(CallRecord(mobility_aid="walker")).mobility_aid == "walker"
    assert clean_record(CallRecord(mobility_aid="wheelchair")).mobility_aid == "wheelchair"
    # A clinical description in a practical field is dropped, not marked --
    # there's no useful remainder.
    assert clean_record(CallRecord(mobility_aid="paralysed on the left since her stroke")).mobility_aid == ""


def test_type_confusion_does_not_crash():
    rec = from_extraction({"duration_minutes": "180", "needs_help_to_car": "yes",
                           "seconds": None, "caller_name": 12345})
    assert rec.duration_minutes == 180
    assert rec.needs_help_to_car is True
    assert rec.caller_name == "12345"


def test_empty_and_none_are_safe():
    assert scrub("") == ""
    assert scrub(None) is None
    assert generalise_destination("") == ""
    assert from_extraction({}).to_dict()["caller_name"] == ""
    assert from_extraction(None).to_dict()["caller_name"] == ""
