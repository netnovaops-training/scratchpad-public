"""
The local places directory, in memory.

WHY THIS EXISTS
A caller asks "somewhere nice she could sit for a couple of hours?" and the
answer has to arrive in the next breath. Searching the web mid-call means an
80-year-old listening to silence, and a model inventing an address means a
frail person driven to the wrong building.

So config/places.yaml is loaded once at startup and answered from RAM. Every
entry was verified against an official source and carries the URL.

WHAT THIS DOES NOT DO
It never writes anything. Looking up a dialysis centre to find its address is
fine -- that is public information about a company. Writing "dialysis" into a
client record is not, because it records a kidney condition. phi.py generalises
destinations for exactly that reason, and nothing here bypasses it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("telephony.places")

PLACES_FILE = os.environ.get("PLACES_FILE", "/config/places.yaml")

# A SECOND file, merged on top of the first. This split is the important design
# decision in this module, so it's worth explaining.
#
#   places.yaml        hand-curated. Full of comments, warnings and sources
#                      that took real work to establish. NOTHING automated ever
#                      writes to it.
#   places.local.yaml  everything added by the importer or the web page.
#
# The reason is comments. Loading YAML and writing it back destroys every
# comment in the file -- the "Derfelt is not a senior centre any more" warning,
# the source URLs, the note about summer hours. One import would silently strip
# the most valuable part of the file and nobody would notice until someone was
# sent to the wrong building.
#
# Keeping machine writes in a separate file means the curated one is never
# rewritten, and both stay editable in Notepad.
PLACES_LOCAL = os.environ.get("PLACES_LOCAL_FILE", "/config/places.local.yaml")

# A THIRD layer: what you add from the browser at /admin/site/places.
#
# Written by the admin, mounted here read-only. It is JSON rather than YAML for
# one reason: the admin can guarantee JSON is well-formed before it writes, and
# a phone line that cannot load its directory because somebody left a stray tab
# in a YAML file is a phone line that suggests nothing.
#
# ORDER MATTERS AND IT IS DELIBERATE:
#   places.yaml        curated, verified, cited. Always wins.
#   places.local.yaml  the importer's output.
#   places.json        added from the browser.
# A name already present higher up is NOT overwritten from lower down. The
# hand-checked address with a source URL beats one typed into a web form at
# eleven at night, and it beats it silently rather than by asking.
PLACES_JSON = os.environ.get("PLACES_JSON_FILE", "/data/site/places.json")

# How often to notice the file changed. A stat() is nothing; doing one per
# lookup is fine, and it means a place added at 2pm is answerable on a call at
# 2:01 without restarting the phone line.
RELOAD_SECONDS = float(os.environ.get("PLACES_RELOAD_SECONDS", "30"))

# Categories a caller might name, mapped to the sections in the file.
_ALIASES = {
    "senior center": "senior_centers", "senior centre": "senior_centers",
    "seniors": "senior_centers", "activity": "senior_centers",
    "library": "libraries", "books": "libraries",
    "park": "parks", "outside": "parks", "outdoors": "parks", "garden": "parks",
    "museum": "indoor_summer", "mall": "indoor_summer", "indoor": "indoor_summer",
    "inside": "indoor_summer", "air conditioning": "indoor_summer",
    "pharmacy": "pharmacies", "chemist": "pharmacies", "prescription": "pharmacies",
    "drug store": "pharmacies",
    "dialysis": "dialysis",
    "physical therapy": "physical_therapy", "pt": "physical_therapy", "rehab": "physical_therapy",
    "hospice": "hospice",
    "nursing home": "care_homes", "assisted living": "care_homes",
    "retirement": "care_homes", "care home": "care_homes",
    "day care": "adult_day_and_respite", "respite": "adult_day_and_respite",
    "transport": "transport", "bus": "transport", "paratransit": "transport",
    "helpline": "helplines", "help line": "helplines",
}

# Sections a companion would take someone TO for company, as opposed to
# errands, appointments and pickup addresses.
OUTING_SECTIONS = ("senior_centers", "libraries", "parks", "indoor_summer")

# Las Vegas: outdoors is unusable for much of the year. This is not a detail --
# it decides whether the outing works at all.
_HOT_MONTHS = {5, 6, 7, 8, 9}
_COOL_HOURS = range(6, 10)      # 6am-9am is the workable outdoor window


class Places:
    def __init__(
        self,
        path: str | Path | None = None,
        local: str | Path | None = None,
    ) -> None:
        self.path = Path(path or PLACES_FILE)
        self.local = Path(local) if local else Path(PLACES_LOCAL)
        self.json = Path(os.environ.get("PLACES_JSON_FILE", PLACES_JSON))
        self.data: dict[str, Any] = {}
        self._stamp: tuple = ()
        self._checked = 0.0
        self.load()

    @staticmethod
    def _read(path: Path, required: bool) -> dict:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            # Neither file is required. The phone line answers calls without a
            # directory; it just can't suggest anywhere.
            if required:
                log.warning("no places file at %s -- suggestions disabled", path)
            return {}
        except yaml.YAMLError as e:
            # A broken local file must not take out the curated one. Someone
            # mistyping in Notepad should lose their own edit, not 62 verified
            # entries.
            log.error("%s is not valid YAML, ignoring it: %s", path.name, e)
            return {}

    def _read_json(self) -> dict:
        """places.json, regrouped into the same shape as the YAML.

        The admin stores one flat list with a `category` on each entry; the
        YAML is a dict of sections. Regrouping here rather than changing either
        format keeps both files editable by hand in the way their users expect.

        A broken file is logged and ignored, never fatal -- same rule as the
        local YAML. Losing your browser additions is a bad afternoon; losing the
        verified directory is somebody driven to the wrong building.
        """
        try:
            raw = json.loads(self.json.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.error("%s is not valid JSON, ignoring it: %s", self.json.name, e)
            return {}

        rows = raw.get("places") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return {}

        out: dict[str, list] = {}
        for r in rows:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            entry = {k: v for k, v in r.items() if k != "category" and not k.startswith("_")}
            # No category is not an error. indoor_summer is the safe default in
            # Las Vegas: suggesting somewhere indoors when you were not sure is
            # recoverable; suggesting a park in July is not.
            section = str(r.get("category") or "indoor_summer").strip().lower()
            out.setdefault(section, []).append(entry)
        return out

    def _stamp_of(self) -> tuple:
        """(mtime, size) for all three files, so any edit to any of them shows."""
        out = []
        for p in (self.path, self.local, self.json):
            try:
                st = p.stat()
                out.append((st.st_mtime, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def refresh(self) -> None:
        """Re-read if any source file changed. Called before every lookup.

        Cheap enough to do per call (three stat()s), and the alternative is that
        a place added in the admin is not answerable until something restarts --
        which for a phone line means "until the next time it breaks".
        """
        now = time.time()
        if now - self._checked < RELOAD_SECONDS:
            return
        self._checked = now
        stamp = self._stamp_of()
        if stamp != self._stamp:
            self.load()

    def load(self) -> None:
        self.data = self._read(self.path, required=True)
        extra = self._read(self.local, required=False)

        added = 0
        for section, entries in extra.items():
            if not isinstance(entries, list):
                continue
            have = {self._key(e) for e in self.data.get(section, []) if isinstance(e, dict)}
            for e in entries:
                if not isinstance(e, dict):
                    continue
                if self._key(e) in have:
                    continue          # curated entry wins; don't duplicate
                self.data.setdefault(section, []).append(e)
                added += 1

        from_admin = 0
        for section, entries in self._read_json().items():
            if not isinstance(entries, list):
                continue
            known = [e for e in self.data.get(section, []) if isinstance(e, dict)]
            have = {self._key(e) for e in known}
            # NAMES TOO, not just name+street-number. The normal key includes
            # the street number so two branches of the same pharmacy stay
            # separate -- right for the importer, wrong here.
            #
            # A browser entry whose address was mistyped, or left blank, has no
            # street number, so its key does not match the curated one and it
            # sails through as a SECOND "Walnut Recreation Center" carrying the
            # wrong address. Half the callers would then be told the wrong one.
            # Found by a test that typed rubbish into the address on purpose.
            #
            # So: an entry with no usable street number is matched on name
            # alone. Something with no address is not a different branch you
            # could be sent to -- it is the same place, described worse.
            have_names = {self._name_key(e) for e in known}
            for e in entries:
                if self._key(e) in have:
                    continue
                if not self._street_digits(e) and self._name_key(e) in have_names:
                    continue
                self.data.setdefault(section, []).append(e)
                from_admin += 1

        self._stamp = self._stamp_of()
        self._checked = time.time()
        log.info("loaded %d places from %s (+%d local, +%d from the admin)",
                 self.count(), self.path, added, from_admin)

    @staticmethod
    def _name_key(entry: dict) -> str:
        return re.sub(r"[^a-z0-9]", "", str(entry.get("name", "")).lower())

    @staticmethod
    def _street_digits(entry: dict) -> str:
        return re.sub(r"[^0-9]", "", str(entry.get("address", ""))[:12])

    @classmethod
    def _key(cls, entry: dict) -> str:
        """Identity for dedupe: name plus street number, both squashed."""
        return f"{cls._name_key(entry)}|{cls._street_digits(entry)}"

    def count(self) -> int:
        return sum(len(v) for k, v in self.data.items()
                   if isinstance(v, list) and k != "warnings")

    def sections(self) -> list[str]:
        return [k for k, v in self.data.items()
                if isinstance(v, list) and k != "warnings"]

    def all_entries(self, sections: tuple[str, ...] | None = None) -> list[dict]:
        out = []
        for name in (sections or self.sections()):
            for e in self.data.get(name, []):
                if isinstance(e, dict):
                    out.append({**e, "category": name})
        return out

    # -- searching -------------------------------------------------------------

    def resolve_category(self, text: str) -> str | None:
        t = (text or "").lower().strip()
        if t in self.data and isinstance(self.data.get(t), list):
            return t
        for alias, section in _ALIASES.items():
            if alias in t:
                return section
        return None

    def search(self, query: str = "", category: str = "", limit: int = 5) -> list[dict]:
        """
        Score entries against free text. Deliberately simple -- the file is
        small and a real index would be more machinery than it earns.
        """
        self.refresh()
        section = self.resolve_category(category) or self.resolve_category(query)
        entries = self.all_entries((section,) if section else None)

        words = [w for w in re.findall(r"[a-z]+", (query or "").lower()) if len(w) > 2]
        if not words:
            return entries[:limit]

        scored = []
        for e in entries:
            hay = " ".join(str(v) for v in e.values()).lower()
            tags = " ".join(e.get("good_for", [])).lower()
            score = 0
            for w in words:
                if w in e.get("name", "").lower():
                    score += 4
                if w in tags:
                    score += 3
                elif w in hay:
                    score += 1
            # A place that costs nothing is nearly always the better suggestion
            # for this clientele; nudge it up when nothing else separates them.
            if "free" in tags or str(e.get("cost", "")).lower() == "free":
                score += 1

            # Asked for a drive-through? Prefer the ones actually CONFIRMED to
            # have one. Several entries say "unconfirmed", and offering one of
            # those to someone who cannot get out of the car is worse than
            # offering nothing -- they drive there and find they must go in.
            if "drive" in (query or "").lower():
                # NOTE: YAML reads a bare `yes` as the boolean True (YAML 1.1),
                # so this has to accept both forms. Comparing only against the
                # string "yes" silently never matches, and the unconfirmed
                # entries win by default -- which is the opposite of the point.
                dt = e.get("drive_through", "")
                confirmed = dt is True or str(dt).strip().lower() in ("yes", "true")
                if confirmed:
                    score += 6
                else:
                    score -= 3

            # Never lead with something flagged as likely to have moved or
            # closed, when a stable alternative exists.
            if str(e.get("stale", "")).lower() == "high":
                score -= 1

            # When the caller named a category, every entry in it is a valid
            # answer -- score decides the ORDER, not whether it appears at all.
            # Without this floor, the staleness penalty could empty a whole
            # section and "pharmacy" would return nothing.
            if section:
                scored.append((max(score, 1), e))
            elif score > 0:
                scored.append((score, e))

        scored.sort(key=lambda p: -p[0])
        return [e for _, e in scored[:limit]]

    # -- the one that matters --------------------------------------------------

    def suggest_outing(
        self,
        wants: str = "",
        when: datetime | None = None,
        needs_shade: bool = False,
        low_mobility: bool = False,
        limit: int = 3,
    ) -> list[dict]:
        """
        Somewhere to spend a couple of hours.

        The season check is the useful part. Suggesting a park at 2pm in July
        is worse than suggesting nothing -- it sounds helpful and produces a
        miserable afternoon.
        """
        self.refresh()
        now = when or datetime.now()
        hot = now.month in _HOT_MONTHS and now.hour not in _COOL_HOURS

        entries = self.all_entries(OUTING_SECTIONS)
        words = [w for w in re.findall(r"[a-z]+", (wants or "").lower()) if len(w) > 2]

        scored = []
        for e in entries:
            tags = " ".join(e.get("good_for", [])).lower()
            hay = " ".join(str(v) for v in e.values()).lower()
            score = 1

            for w in words:
                if w in tags:
                    score += 4
                elif w in hay:
                    score += 1

            if hot:
                # Indoors, or shade you can rely on.
                if e["category"] in ("indoor_summer", "libraries", "senior_centers"):
                    score += 6
                elif "shade" in tags or "ramada" in tags:
                    score += 2
                else:
                    score -= 4
            if needs_shade and ("shade" in tags or e["category"] != "parks"):
                score += 2
            if low_mobility:
                for good in ("no_stairs", "flat_paths", "drive_up_picnic",
                             "free_wheelchairs", "accessible_parking",
                             "fully_accessible", "benches", "seating"):
                    if good in tags:
                        score += 3
            if "free" in tags or str(e.get("cost", "")).lower() == "free":
                score += 2

            scored.append((score, e))

        scored.sort(key=lambda p: -p[0])
        out = [e for _, e in scored[:limit]]

        if hot and out:
            for e in out:
                e = e.setdefault("_advice", "")
            out[0]["_advice"] = (
                "It's the hot part of the year -- outdoors really only works "
                "between about 6 and 9 in the morning."
            )
        return out

    def warnings(self) -> list[str]:
        return list(self.data.get("warnings", []))


def format_for_speech(entries: list[dict]) -> str:
    """
    Turn results into something that can be said out loud.

    Not a list. Reading a caller five options with addresses is how you lose
    someone on the phone -- two or three, named, with one reason each.
    """
    if not entries:
        return "I don't have anywhere in my list for that."
    bits = []
    for e in entries[:3]:
        why = ""
        tags = e.get("good_for", [])
        if "free_wheelchairs" in tags:
            why = " — they have wheelchairs you can borrow"
        elif "drive_up_picnic" in tags:
            why = " — you can drive right up to the tables"
        elif "shade" in tags or "reservable_ramadas" in tags:
            why = " — there's proper shade"
        elif "quiet_room" in tags or "quiet" in tags:
            why = " — it's quiet"
        elif "no_stairs" in tags:
            why = " — no stairs anywhere"
        elif str(e.get("cost", "")).lower() == "free":
            why = " — and it's free"
        bits.append(f"{e['name']}{why}")
    return "; ".join(bits)


# The tool the phone agent calls.
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_places",
        "description": (
            "Look up real local places in North Las Vegas and the Las Vegas "
            "valley: somewhere to spend time (senior centres, libraries, parks, "
            "museums), or a pharmacy, dialysis centre or clinic. Use this "
            "instead of guessing an address -- every entry here is verified. "
            "For 'somewhere nice to go', set purpose='outing'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What they're after: 'somewhere quiet', 'shade', 'pharmacy with a drive through', 'cards'",
                },
                "purpose": {
                    "type": "string",
                    "enum": ["outing", "errand", "appointment", "any"],
                    "description": "outing = somewhere to spend time together",
                },
                "low_mobility": {
                    "type": "boolean",
                    "description": "True if they use a walker or wheelchair, or can't walk far from the car",
                },
            },
            "required": ["query"],
        },
    },
}
