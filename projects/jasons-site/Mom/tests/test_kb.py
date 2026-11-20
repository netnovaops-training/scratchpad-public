"""
Knowledge base tests.

WHAT IS ACTUALLY AT RISK HERE
The knowledge base can skip the model entirely, which means it can also skip
the model's chance to notice the question was about something else. So the
tests that matter most are not "does a match work" -- they are the four ways a
match should NOT happen:

    1. an article that is a near-miss answers anyway
    2. an article you have not vetted (`direct` unset) answers anyway
    3. a broken or half-written file empties the knowledge base mid-question
    4. a question with a word in common with everything matches everything

Getting any of those wrong produces a confident, instant, wrong answer to
someone who has no way to tell it is wrong. That is worse than no feature.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import kb  # noqa: E402


PRINTER = {
    "id": "printer-offline",
    "title": "Printer says offline but it is switched on",
    "category": "printer",
    "symptoms": ["the printer says offline", "nothing comes out of the printer"],
    "keywords": ["printer", "offline"],
    "steps": ["Turn the printer off at the switch and count to ten.",
              "Turn it back on and wait for the lights to settle."],
    "answer": "It is almost always the printer losing the Wi-Fi.",
    "direct": True,
    "source": "ticket TH-0042",
}

WIFI = {
    "id": "tablet-wifi",
    "title": "Tablet will not join the Wi-Fi",
    "category": "internet",
    "symptoms": ["the tablet will not join the wifi",
                 "my tablet cannot connect to the internet"],
    "keywords": ["tablet", "wifi", "internet"],
    "steps": ["Turn the tablet off and on again."],
    "direct": True,
}

# Written but not vetted: no "direct" key at all.
UNVETTED = {
    "id": "email-password",
    "title": "Email keeps asking for a password",
    "symptoms": ["email keeps asking for my password"],
    "steps": ["Sign in on the website first."],
}


@pytest.fixture
def base(tmp_path, monkeypatch):
    """A knowledge base at a temp path, with the module cache cleared."""

    def write(articles, wrapper="articles"):
        path = tmp_path / "mom-kb.json"
        body = {wrapper: articles} if wrapper else articles
        path.write_text(json.dumps(body), encoding="utf-8")
        monkeypatch.setattr(kb, "KB_FILE", path)
        kb._cache.update({"stamp": None, "checked": 0.0, "articles": []})
        kb.load(force=True)
        return path

    return write


# --- loading -----------------------------------------------------------------

def test_articles_load_from_the_file(base):
    base([PRINTER, WIFI])
    assert kb.count() == 2
    assert kb.get("printer-offline")["title"].startswith("Printer says offline")


def test_a_bare_list_is_accepted_as_well_as_an_articles_object(base):
    base([PRINTER], wrapper=None)
    assert kb.count() == 1


def test_an_article_with_no_answer_and_no_steps_is_dropped(base):
    # It cannot answer anything, and counting it would make the admin's "3
    # articles" disagree with what the app can actually do.
    base([PRINTER, {"title": "Half written", "symptoms": ["something"]}])
    assert kb.count() == 1


def test_keywords_written_as_one_comma_string_still_work(base):
    # A pasted article from a model does this perhaps a third of the time.
    # Refusing it would mean an article that silently never matches.
    base([{**PRINTER, "keywords": "printer, offline, hp"}])
    assert "printer" in kb.load()[0]["keywords"]


def test_an_id_is_derived_when_the_file_does_not_carry_one(base):
    base([{k: v for k, v in PRINTER.items() if k != "id"}])
    assert kb.load()[0]["id"]


def test_a_missing_file_is_an_empty_knowledge_base_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "KB_FILE", tmp_path / "never-written.json")
    kb._cache.update({"stamp": None, "checked": 0.0, "articles": []})
    assert kb.load(force=True) == []
    assert kb.direct_answer("the printer says offline") is None


def test_a_broken_file_keeps_the_last_good_articles(base, monkeypatch):
    # This file is read while the admin is writing it. A half-written read must
    # not empty the knowledge base in the middle of her asking something.
    path = base([PRINTER])
    assert kb.count() == 1

    path.write_text("{ not json at all", encoding="utf-8")
    kb._cache["checked"] = 0.0
    assert kb.load()  # still one article
    assert kb.direct_answer("the printer says offline") is not None


# --- what should match -------------------------------------------------------

def test_a_symptom_written_word_for_word_is_answered_directly(base):
    base([PRINTER, WIFI])
    hit = kb.direct_answer("the printer says offline")
    assert hit and hit["article"]["id"] == "printer-offline"


def test_the_words_she_would_actually_use_still_match(base):
    base([PRINTER, WIFI])
    hit = kb.direct_answer("nothing comes out of the printer at all today")
    assert hit and hit["article"]["id"] == "printer-offline"


def test_the_right_article_wins_when_two_are_close(base):
    base([PRINTER, WIFI])
    hit = kb.direct_answer("my tablet cannot connect to the internet")
    assert hit and hit["article"]["id"] == "tablet-wifi"


# --- what should NOT match ---------------------------------------------------

def test_an_article_you_have_not_vetted_is_never_sent_as_the_answer(base):
    # A perfect match on an article with no "direct": true must still go to the
    # model. This is the opt-in that makes writing an article in a hurry safe.
    base([UNVETTED])
    assert kb.direct_answer("email keeps asking for my password") is None
    # But it is still offered to the model as context.
    assert "Email keeps asking" in kb.recall_block("email keeps asking for my password")


def test_a_vaguely_related_question_is_left_to_the_model(base):
    base([PRINTER, WIFI])
    assert kb.direct_answer("can I print on both sides of the paper?") is None


def test_an_unrelated_question_matches_nothing_at_all(base):
    base([PRINTER, WIFI])
    article, s = kb.best("how long do I roast a chicken")
    assert article is None and s == 0.0


def test_one_shared_common_word_is_not_a_match(base):
    # "the", "my", "how" are stripped; a single incidental overlap must not
    # drag an article to the top of an otherwise empty ranking.
    base([PRINTER])
    assert kb.direct_answer("how do I turn the television on") is None


def test_an_empty_question_matches_nothing(base):
    base([PRINTER])
    assert kb.best("") == (None, 0.0)
    assert kb.recall_block("") == ""


def test_raising_the_threshold_makes_it_more_cautious(base, monkeypatch):
    base([PRINTER])
    assert kb.direct_answer("the printer says offline")
    monkeypatch.setattr(kb, "DIRECT_SCORE", 0.99)
    assert kb.direct_answer("the printer says offline") is None


# --- what the model and she actually see -------------------------------------

def test_the_rendered_answer_carries_the_steps_in_order(base):
    base([PRINTER])
    out = kb.render(kb.get("printer-offline"))
    assert "1. Turn the printer off" in out
    assert "2. Turn it back on" in out
    assert out.index("1. Turn") < out.index("2. Turn")


def test_the_prompt_block_tells_the_model_to_ignore_a_bad_fit(base):
    # The single most important sentence in the block. Without it a near-match
    # becomes a confidently wrong answer in the model's own voice.
    base([PRINTER])
    block = kb.recall_block("the printer says offline")
    assert "ignore it completely" in block


def test_the_prompt_block_is_empty_when_nothing_is_relevant(base):
    base([PRINTER])
    assert kb.recall_block("how long do I roast a chicken") == ""


def test_stats_separate_vetted_articles_from_the_rest(base):
    base([PRINTER, WIFI, UNVETTED])
    st = kb.stats()
    assert st["total"] == 3
    assert st["direct"] == 2, "only the vetted ones can answer without the model"


# --- picking up a change -----------------------------------------------------

def test_an_article_added_after_startup_is_used_without_a_restart(base):
    path = base([PRINTER])
    assert kb.direct_answer("my tablet cannot connect to the internet") is None

    path.write_text(json.dumps({"articles": [PRINTER, WIFI]}), encoding="utf-8")
    kb._cache["checked"] = 0.0     # skip the poll interval; the point is reload

    assert kb.direct_answer("my tablet cannot connect to the internet")


def test_deleting_an_article_really_stops_it_answering(base):
    path = base([PRINTER, WIFI])
    assert kb.direct_answer("the printer says offline")

    path.write_text(json.dumps({"articles": [WIFI]}), encoding="utf-8")
    kb._cache["checked"] = 0.0

    assert kb.direct_answer("the printer says offline") is None
    assert kb.count() == 1
