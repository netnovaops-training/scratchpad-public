#!/usr/bin/env python3
"""Decide what an arriving file IS and who it belongs to. Nothing else.

Jason, 2026-09-11: "tagging them based on a tag if I say Z and then send the
file tag them automatically based on what's the file content using my AI
models ... automating all the process for me without me doing all the
manual labor".

===========================================================================
THE TWO RULES THAT SHAPE EVERYTHING BELOW.

1. THE PERSON IS NEVER INFERRED FROM CONTENT. Only three things can name a
   person: an explicit tag in the caption ("Z"), the account that sent it,
   or nothing. A model reading a PDF and deciding it "feels like Alex's"
   is how a medical record ends up in a teenager's library. This is the
   same asymmetry paperless_autotag.py already enforces -- the TYPE is
   inferred, the PERSON is read off a fact. Getting a type wrong is untidy.
   Getting a person wrong is a disclosure.

2. THE VOCABULARY IS CLOSED. The model chooses from the libraries and tags
   that ALREADY EXIST. It cannot invent "Manga - Horror" or "Alexs Meds".
   An open-vocabulary classifier produces a long tail of near-duplicate
   categories that nobody ever cleans up, and Paperless's own
   auto-matching was switched off on this stack for exactly this reason
   after it filed a nursing test bank under "Jasons Shelf".

Unknown is a valid answer and lands in unsorted/. It is not a failure --
it is the classifier declining to guess, which is the behaviour that makes
the other answers worth trusting.
===========================================================================

CBZ AND CBR ARE NEVER UNPACKED. Jason asked for files to arrive
"uncompressed", and for a comic that would break it. A .cbz IS a zip of
page images -- that is the entire format. Kavita opens the archive and
pages through it; handed a folder of JPEGs instead it sees a hundred
separate one-page entries. So "uncompressed" is handled at the TELEGRAM
end (send as File, not as Photo, so Telegram does not re-encode), and the
archive itself is copied through untouched.

WHY A LOCAL MODEL AND NOT A PAID ONE. Everything classified here is
household paperwork -- medical records, pay stubs, tax returns. Ollama on
the PC never leaves the house. The classification is also easy: it is a
handful of categories from a closed list, which an 8B model does reliably
and a frontier model would do no better.

qwen2.5, NOT qwen3. litellm/config.yaml carries the warning already:
qwen3 is a THINKING model and wraps its answer in reasoning before
committing. For a JSON-shaped answer that is a parsing problem, not a
feature.
"""
from __future__ import annotations

import json
import os
import re
import time
import zipfile

import httpx

LLM_BASE = os.environ.get("LLM_BASE", "https://llm.home.example.net")
LLM_KEY = os.environ.get("LITELLM_KEY", "")
# qwen2.5:7b. Benchmarked against the alternatives from inside this
# container on 2026-09-11, on 200/2500/6000-character prompts:
#
#   fast (llama3.2:3b)     0.3s, but answered "Contract" -- a word that is
#                          not in the closed vocabulary, so it would be
#                          thrown away and everything filed as unknown.
#   mama (qwen2.5:7b)      0.3-0.4s, clean in-vocabulary answers throughout.
#   big-context (14b)      13.7s on the FIRST call, 0.5s after.
#
# That 13.7s is the model cold-loading into VRAM, and it is what produced
# the RemoteProtocolError this classifier hit on its first real PDF: some
# hop between here and Ollama -- LiteLLM, Caddy, or the relay -- gave up
# and closed the connection. A 7B model loads fast enough not to provoke
# it, and is no less accurate on a four-way choice.
LLM_MODEL = os.environ.get("INTAKE_MODEL", "mama")
LLM_TIMEOUT = float(os.environ.get("INTAKE_LLM_TIMEOUT", "120"))

# --------------------------------------------------------------------------
# WHO. A caption tag or the sending account, and nothing else.
#
# Short forms because Jason asked for them -- "if I say Z". They are matched
# as the WHOLE caption or as a leading #hashtag, never as a substring: a
# caption reading "Zombie Survival Guide" must not become Casey's.
# --------------------------------------------------------------------------
PEOPLE = {
    "z": "casey", "casey": "casey", "zanetta": "casey",
    "s": "alex", "alex": "alex", "sash": "alex",
    "j": "jason", "jason": "jason", "me": "jason",
    "m": "mom", "mom": "mom", "davetta": "mom", 'robin': 'robin', 'j u n': 'robin',
}

# Where each person's things live, mirroring the mounts Kavita already has.
SHELF = {
    "casey": dict(manga="/srv/manga/casey", books="/srv/books/casey"),
    "alex": dict(manga="/srv/manga/alex", books="/srv/books/alex"),
    "jason": dict(manga="/srv/manga/jason", books="/srv/books/jason"),
    "mom": dict(manga="/srv/manga/mom", books="/srv/books/mom"),
    # T0925 part 2 (A12, 2026-11-19): robin was in PEOPLE and portal.WHO but had no shelf, so his books
    # went to unsorted. /home/user/documents/robin already exists (owner server, 700). Kavita does not
    # mount it yet (only jason, alex, casey); that is a separate item.
    "robin": dict(manga="/srv/manga/robin", books="/srv/books/robin"),
}

# Where a kind waits when it is correctly identified but has nowhere to
# be filed yet, or when the uploader may read that library but not write
# to it. Per person, so one person's held items are never another's.
PENDING_ROOT = os.environ.get("PENDING_ROOT", "/srv/pending")

COMIC = {".cbz", ".cbr", ".cb7"}
BOOK = {".epub", ".mobi", ".azw3", ".djvu"}
DOCISH = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt"}

# The closed vocabulary. Anything the model returns that is not in here is
# discarded and the file goes to unsorted.
KINDS = {
    "comic": "a manga volume or comic book",
    "book": "a book, textbook, manual or long reference work",
    "document": "paperwork -- a receipt, bill, statement, letter, form, "
                "tax document, medical record, certificate or report",
    # Added 2026-09-26. `find` has indexed recipes and workouts as
    # first-class kinds since it was built, so those categories existed
    # downstream with no way in: anything dropped here became a
    # "document". Extending the closed vocabulary is a deliberate act --
    # see the note at the top of this file about why it stays closed.
    "recipe": "a recipe -- ingredients and a method for cooking something",
    "workout": "a training plan, exercise routine, or programme of lifts, "
               "sets and reps",
    "unknown": "cannot tell, or it is none of the above",
}

# WHICH GRANT GOVERNS EACH KIND.
#
# Jason, 2026-09-26: "if I haven't given access to her recipe on my SSO
# then I don't want them to add [one]... but have the read access to that
# through my admin management".
#
# These are the app names his admin panel already writes into `apps`, so a
# permission is changed in one place and this follows it. A kind with no
# entry here is ungoverned and goes to unsorted, which is the safe default:
# a new kind cannot accidentally be writable by everybody.
GRANT_FOR_KIND = {
    "recipe": "recipe",
    "workout": "gym",
    "document": "paperless",
    "book": "read",
    "comic": "read",
}


def person_from_caption(caption: str | None) -> str | None:
    """'Z', '#z', 'z - vol 3' -> casey. 'Zombie Guide' -> None."""
    if not caption:
        return None
    head = caption.strip().lower()
    # A leading #hashtag, or the first word before a separator.
    m = re.match(r"^#?([a-z]+)\b", head)
    if not m:
        return None
    word = m.group(1)
    # Only treat a bare word as a person tag if the caption IS that word,
    # or the word is followed by a separator. "z - vol 3" yes.
    # "zombie survival" no, because the match must be the whole word AND
    # the caption must not simply continue into more letters.
    if word in PEOPLE:
        rest = head[m.end():].lstrip()
        if not rest or rest[0] in "-–—:,/|#":
            return PEOPLE[word]
        # A longer caption starting with a full name is still that person:
        # "alex microbiology notes".
        if len(word) > 2:
            return PEOPLE[word]
    return None


def looks_like_comic(path: str, ext: str) -> bool:
    """A .cbz is a zip of images. Verify that rather than trusting the name.

    A .pdf renamed to .cbz would otherwise be handed to Kavita's manga
    library, which would show an unreadable entry with no obvious cause.
    """
    if ext in COMIC:
        return True
    if ext == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                names = [n.lower() for n in z.namelist()[:40]]
            imgs = sum(n.endswith((".jpg", ".jpeg", ".png", ".webp", ".avif"))
                       for n in names)
            return bool(names) and imgs / len(names) > 0.8
        except Exception:
            return False
    return False


PROMPT = """You classify a file for a private household library.

Answer with ONE of these words and nothing else:
{kinds}

Rules:
- Answer with a single word from the list. No explanation, no punctuation.
- "document" means household paperwork someone files and retrieves later.
- "book" means something someone sits and reads.
- If the text is empty, garbled, or you are not confident, answer: unknown

Filename: {name}

First part of the text:
---
{text}
---

Answer with one word:"""


def ask_model(name: str, text: str) -> str:
    """One word back, from a closed list. Temperature 0.

    TEMPERATURE 0 IS NOT A STYLE CHOICE. Jason, on the study tool: "I want
    to make sure the temp is set to 0 so that he is given facts ... and
    it's not made up info". The same applies to a filing decision -- a
    classifier that answers differently on Tuesday is one you cannot debug.
    """
    kinds = "\n".join(f"  {k} = {v}" for k, v in KINDS.items())
    body = {
        "model": LLM_MODEL,
        "temperature": 0,
        "max_tokens": 8,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(kinds=kinds, name=name,
                                     text=text[:6000] or "(no text found)"),
        }],
    }
    headers = {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}
    # NEVER RAISES. An intake pipeline that 500s because the classifier
    # is unreachable is one that drops the user's file on the floor -- and
    # the model lives on the PC, which is a machine that sleeps. The
    # correct behaviour when the model cannot be reached is the same as
    # when it says so: "unknown", which sends the file to unsorted with a
    # note. Unsorted is recoverable. A 500 and a discarded upload is not.
    # ONE RETRY, and only one. The failure this guards against is a cold
    # model load: the first request after Ollama has evicted the weights
    # can take ten seconds or more and get its connection closed from
    # under it, while the second -- with the model now resident -- returns
    # in under a second. Retrying more than once would just be waiting on
    # something genuinely broken while a user's file sits in a temp dir.
    last = None
    for attempt in (1, 2):
        try:
            r = httpx.post(f"{LLM_BASE}/v1/chat/completions", json=body,
                           headers=headers, timeout=LLM_TIMEOUT)
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"]
            break
        except Exception as e:
            last = e
            if attempt == 2:
                return f"unknown:{type(e).__name__}"
            time.sleep(2)

    # Strip a thinking block if a thinking model is configured after all --
    # cheap insurance against the model behind "big-context" being changed.
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    word = re.sub(r"[^a-z]", "", out.strip().lower().split()[0]) if out.strip() else ""
    return word if word in KINDS else "unknown"


def extract_text(path: str, ext: str, limit: int = 6000) -> str:
    """Enough text for the model to recognise the shape of the thing."""
    if ext in {".txt", ".md", ".csv", ".rtf"}:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except Exception:
            return ""
    if ext == ".pdf":
        try:
            import pypdf
            r = pypdf.PdfReader(path)
            out = []
            for page in r.pages[:6]:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) > limit:
                    break
            return "\n".join(out)[:limit]
        except Exception:
            return ""
    if ext == ".epub":
        try:
            with zipfile.ZipFile(path) as z:
                html = [n for n in z.namelist()
                        if n.lower().endswith((".xhtml", ".html", ".htm"))][:4]
                txt = " ".join(
                    re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "replace"))
                    for n in html)
            return re.sub(r"\s+", " ", txt)[:limit]
        except Exception:
            return ""
    return ""


def classify(path: str, filename: str, caption: str | None,
             sender_person: str | None) -> dict:
    """Everything the router needs, and an honest note about how it decided.

    `why` is returned for every decision and is written into the log. A
    filing system nobody can interrogate is one nobody trusts, and the
    first question when something lands in the wrong place is always
    "why did it do that".
    """
    ext = os.path.splitext(filename)[1].lower()
    person = person_from_caption(caption) or sender_person
    why = []

    if person_from_caption(caption):
        why.append(f"caption named {person}")
    elif sender_person:
        why.append(f"sender is {sender_person}")
    else:
        why.append("no person -- caption named nobody and the sender is "
                   "not mapped")

    # --- type, cheaply first ------------------------------------------
    # Extension and magic bytes settle most of it without waking a model.
    if looks_like_comic(path, ext):
        kind = "comic"
        why.append("comic by extension" if ext in COMIC
                   else "zip of images, so a comic")
    elif ext in BOOK:
        kind = "book"
        why.append(f"{ext} is a book format")
    elif ext in DOCISH:
        text = extract_text(path, ext)
        if not text.strip():
            # A PDF with no text layer is a scan. Paperless OCRs; Kavita
            # does not. So a scan is paperwork until proven otherwise.
            kind = "document"
            why.append("no text layer -- a scan, which is Paperless's job")
        else:
            kind = ask_model(filename, text)
            if kind.startswith("unknown:"):
                why.append(f"model UNREACHABLE ({kind.split(':',1)[1]}) "
                           f"-- unsorted rather than guessed")
                kind = "unknown"
            else:
                why.append(f"model read {len(text)} chars -> {kind}")
    else:
        kind = "unknown"
        why.append(f"{ext or 'no extension'} is not a type we place")

    # --- where -----------------------------------------------------------
    dest = None
    if kind in ("comic", "book") and person and person in SHELF:
        dest = SHELF[person]["manga" if kind == "comic" else "books"]
    elif kind == "document":
        # Into Paperless, which then applies the tag -> owner -> app
        # routing that already exists. This module does not duplicate it.
        dest = "/srv/consume"
        why.append("Paperless decides its tag and owner from there")
    elif kind in ("recipe", "workout") and person:
        # NOT written into the recipe app or the workout feed. Those own
        # their records and EXPORT a feed to the index -- there is no
        # folder to drop a file into, and inventing one would put a file
        # somewhere nothing reads. It waits per-person instead, and the
        # remaining hop is an import call against those apps. See TODO.md.
        dest = "{}/{}/{}".format(PENDING_ROOT, person, kind)
        why.append("{} needs an import into its own app; held for {}"
                   .format(kind, person))
    if dest is None:
        why.append("UNSORTED: " + (
            "no person, and a book needs one to have a shelf"
            if kind in ("comic", "book") else "nothing to do with it"))

    return {
        "kind": kind,
        "person": person,
        "dest": dest,
        "unpack": False,  # never. See the header.
        "why": "; ".join(why),
    }
