"""
Consent tests.

The important cases are the ones where someone did NOT agree, because that's
the direction with legal consequences. A consent check that leans towards "yes"
when it's unsure is worse than no check at all -- it manufactures a record
saying someone agreed when they didn't.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import UTC

from app.consent import ConsentStore, _luhn, interpret, purge_transcripts, redact


# --- yes ---------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "yes", "Yes.", "yeah", "yep", "sure", "okay", "ok that's fine",
    "alright", "go ahead", "of course", "please do",
    # Contain a refusal word but mean yes -- the case that broke first time.
    "I don't mind", "I don't care", "no problem", "why not", "no objection",
])
def test_ways_people_say_yes(said):
    granted, method, _ = interpret("", said)
    assert granted is True, said
    assert method == "spoken"


def test_any_key_counts():
    for key in ("1", "5", "0", "#", "*"):
        granted, method, heard = interpret(key, "")
        assert granted is True
        assert method == "keypad"
        assert heard == key


# --- no, and the ambiguous middle --------------------------------------------
@pytest.mark.parametrize("said", [
    "no", "nope", "no thank you", "I'd rather not", "please don't",
    "do not record me", "that's private", "stop",
])
def test_ways_people_say_no(said):
    granted, _, _ = interpret("", said)
    assert granted is False, said


def test_dont_mind_is_yes_not_no():
    """
    'I don't mind' is how a lot of people over seventy say yes. Reading the
    'don't' and recording a refusal would be wrong in the direction that
    loses you a transcript -- but wrong.
    """
    granted, _, _ = interpret("", "oh, I don't mind at all dear")
    assert granted is True


def test_refusal_wins_when_both_words_appear():
    """
    'no, that's fine' contains 'no' AND 'fine'. If the yes-matcher ran first
    this would be recorded as consent from someone who said no.
    """
    granted, _, _ = interpret("", "no, that's fine, I'd rather you didn't")
    assert granted is False


def test_silence_is_not_consent():
    granted, method, _ = interpret("", "")
    assert granted is False
    assert method == "none"


def test_confused_answer_is_not_consent():
    """An elderly caller who doesn't understand the question hasn't agreed."""
    for said in ("what?", "hello? hello?", "I can't hear you", "who is this"):
        granted, _, _ = interpret("", said)
        assert granted is False, said


# --- storage decision --------------------------------------------------------
def test_store_records_and_reads_back(tmp_path):
    s = ConsentStore(tmp_path / "c.db")
    s.record("CA1", "+17025551234", True, "keypad", "1")
    s.record("CA2", "+17025559999", False, "none", "")
    assert s.granted("CA1") is True
    assert s.granted("CA2") is False
    assert s.granted("CA-never-seen") is False, "unknown call must default to NO"


# --- redaction ---------------------------------------------------------------
def test_redacts_ssn():
    assert "[redacted-ssn]" in redact("my social is 123-45-6789")
    assert "123-45-6789" not in redact("my social is 123-45-6789")


def test_redacts_a_real_card_number():
    # A valid Luhn test number.
    out = redact("card 4539578763621486 expires soon")
    assert "[redacted-card]" in out


def test_does_not_mangle_phone_numbers_or_money():
    """
    The reason card matching is Luhn-checked. Without it, every phone number
    and dollar figure in a transcript turns into [redacted-card] and the
    transcript becomes useless.
    """
    for keep in ("call me on 555-0165", "it was $1,250.00", "born 1/2/1946",
                 "appointment at 2 30 on the 14th"):
        assert "[redacted]" not in redact(keep)
        assert "redacted-card" not in redact(keep), keep


def test_luhn():
    assert _luhn("4539578763621486")
    assert not _luhn("4539578763621487")


def test_redact_handles_empty():
    assert redact("") == ""
    assert redact(None) is None


# --- retention ---------------------------------------------------------------
def test_purge_only_touches_phone_transcripts(tmp_path):
    """
    Hermes and Telegram transcripts are the owner's own conversations with his
    own assistant. No third party, no consent question, no reason to delete.
    """
    for surface in ("phone", "hermes", "telegram"):
        d = tmp_path / surface / "2020-01-01"
        d.mkdir(parents=True)
        (d / "old.jsonl").write_text("{}")

    removed = purge_transcripts(tmp_path, days=30)

    assert removed == 1
    assert not (tmp_path / "phone" / "2020-01-01" / "old.jsonl").exists()
    assert (tmp_path / "hermes" / "2020-01-01" / "old.jsonl").exists()
    assert (tmp_path / "telegram" / "2020-01-01" / "old.jsonl").exists()


def test_purge_keeps_recent(tmp_path):
    from datetime import datetime
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    d = tmp_path / "phone" / today
    d.mkdir(parents=True)
    (d / "today.jsonl").write_text("{}")
    assert purge_transcripts(tmp_path, days=30) == 0
    assert (d / "today.jsonl").exists()
