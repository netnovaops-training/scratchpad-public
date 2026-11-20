#!/usr/bin/env python3
"""
Add places from Google Maps.

Three ways in, in order of how much setup they need:

  1. A LIST YOU BUILT IN GOOGLE MAPS  (no API key, no billing, no setup)
       Star places on your phone as you come across them, then export the
       list and import the file. This is the one to use.

  2. A GOOGLE MAPS LINK               (no API key)
       Paste the share link for one place. Gets you the name and enough to
       find it; you fill in the phone by hand.

  3. THE PLACES API                   (needs a Google Cloud key)
       "every pharmacy in 89030" in one command, with phone numbers and
       opening hours attached. 10,000 free searches a month, which is far
       more than this will ever use.

EVERYTHING IS WRITTEN TO places.local.yaml, NEVER TO places.yaml.

That matters. places.yaml holds hand-checked entries, source URLs, and
warnings like "Derfelt is not a senior centre any more". Loading YAML and
writing it back destroys every comment in the file. So this never opens it.
The two files are merged when the phone line starts, and the curated one wins
on conflicts.

    python places_import.py takeout  "C:\\Users\\you\\Downloads\\Saved Places.csv" --category parks
    python places_import.py url      "https://maps.app.goo.gl/xxxx" --category restaurants
    python places_import.py search   "pharmacy in North Las Vegas NV" --category pharmacies --key AIza...
    python places_import.py list     --category parks
    python places_import.py remove   "Name Of Place"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


try:
    import yaml
except ImportError:
    sys.exit("pyyaml is missing:  pip install pyyaml")

LOCAL = Path(os.environ.get("PLACES_LOCAL_FILE", "config/places.local.yaml"))
CURATED = Path(os.environ.get("PLACES_FILE", "config/places.yaml"))

# Sections that already exist, plus room for the ones he'll want next.
KNOWN_SECTIONS = [
    "senior_centers", "libraries", "parks", "indoor_summer",
    "adult_day_and_respite", "helplines", "transport",
    "pharmacies", "dialysis", "physical_therapy", "hospice", "care_homes",
    # new, empty until you add to them
    "doctors", "grocery", "restaurants", "places_of_worship", "salons", "other",
]


# --- the file ----------------------------------------------------------------

def load_local() -> dict:
    if not LOCAL.exists():
        return {}
    try:
        return yaml.safe_load(LOCAL.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        sys.exit(f"{LOCAL} is not valid YAML: {e}\nFix or delete it and try again.")


def load_curated() -> dict:
    if not CURATED.exists():
        return {}
    try:
        return yaml.safe_load(CURATED.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def save_local(data: dict) -> None:
    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# =============================================================================\n"
        "#  Places added by the importer or the web page.\n"
        "#\n"
        "#  Safe to edit in Notepad. Safe to delete -- you'd only lose what's here,\n"
        "#  not the curated list in places.yaml.\n"
        "#\n"
        "#  This file is REWRITTEN by the importer, so any comments you add here will\n"
        "#  be lost. Put notes in the `notes:` field of an entry instead, which\n"
        "#  survives.\n"
        "#\n"
        "#  Entries here are merged UNDER places.yaml: if a place exists in both, the\n"
        "#  hand-checked one wins.\n"
        "# =============================================================================\n\n"
    )
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    LOCAL.write_text(header + body, encoding="utf-8")


def key_of(entry: dict) -> str:
    name = re.sub(r"[^a-z0-9]", "", str(entry.get("name", "")).lower())
    addr = re.sub(r"[^0-9]", "", str(entry.get("address", ""))[:12])
    return f"{name}|{addr}"


def add_entries(section: str, entries: list[dict]) -> tuple[int, int]:
    """Returns (added, skipped-as-duplicate)."""
    local = load_local()
    curated = load_curated()

    seen = {key_of(e) for e in curated.get(section, []) if isinstance(e, dict)}
    seen |= {key_of(e) for e in local.get(section, []) if isinstance(e, dict)}

    # A place can legitimately sit in two sections -- a senior centre is also
    # somewhere to go indoors in August. But the same name landing in
    # `grocery` AND `restaurants` is nearly always a mis-picked --category, so
    # say so rather than silently building a confusing directory.
    elsewhere = {}
    for src in (curated, local):
        for other, items in src.items():
            if other == section or not isinstance(items, list):
                continue
            for e in items:
                if isinstance(e, dict):
                    elsewhere.setdefault(key_of(e), set()).add(other)

    added = skipped = 0
    notes = []
    for e in entries:
        if not e.get("name"):
            continue
        k = key_of(e)
        if k in seen:
            skipped += 1
            continue
        if k in elsewhere:
            notes.append(f"{e['name']} — already listed under {', '.join(sorted(elsewhere[k]))}")
        seen.add(k)
        local.setdefault(section, []).append(e)
        added += 1

    if added:
        save_local(local)
    for n in notes:
        print(f"  note: {n}")
    return added, skipped


# --- 1. a list exported from Google Maps -------------------------------------

def from_takeout(path: Path, category: str) -> list[dict]:
    """
    Read a saved-list CSV exported from Google Maps.

    Google's export is thin on purpose -- usually just Title, Note and URL,
    with no address or phone. So this pulls what it can and marks the entry
    `needs_detail: true` rather than pretending it's complete. A place with no
    address is useless for driving to, and silently importing one would be
    worse than refusing.
    """
    def _flat(v) -> str:
        # csv.DictReader puts OVERFLOW fields in a list under the None key when
        # a row has more commas than the header. Google Maps URLs contain
        # commas in their coordinates -- "?q=36.2761,-115.1928" -- so an
        # unquoted export hits this immediately and crashes on .strip().
        if isinstance(v, (list, tuple)):
            return ",".join(str(x) for x in v if x).strip()
        return str(v or "").strip()

    out = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            low = {str(k or "").strip().lower(): _flat(v) for k, v in row.items()}

            # An unquoted Google URL splits ACROSS columns rather than only
            # overflowing past them, so stitching one named field isn't enough.
            # Searching the whole row as one string catches it however it broke.
            whole = ",".join(_flat(v) for v in row.values())

            name = low.get("title") or low.get("name") or ""
            if not name:
                continue

            address = low.get("address") or low.get("formatted_address") or ""
            url = low.get("url") or low.get("google maps url") or ""

            # Google often has no address column at all, but hides coordinates
            # in the URL. Not a street address, but enough to navigate to --
            # better than an entry nobody can drive to.
            if not address:
                m = re.search(r"[?&@]q?=?([-\d]{1,3}\.\d{3,}),\s*([-\d]{1,4}\.\d{3,})", whole)
                if m:
                    address = f"({m.group(1)}, {m.group(2)})"
            if not url:
                m = re.search(r"(https?://\S+)", whole)
                if m:
                    url = m.group(1).rstrip(",")

            entry = {
                "name": name,
                "source": url or "Google Maps saved list",
                "added": str(date.today()),
            }
            if address:
                entry["address"] = address
            if low.get("note"):
                entry["notes"] = low["note"]
            if not address:
                entry["needs_detail"] = True
            out.append(entry)
    return out


# --- 2. a single share link --------------------------------------------------

def from_url(url: str) -> list[dict]:
    """
    Pull what can be read from a Google Maps link without an API key.

    Short goo.gl links are followed to their full form first. This gets a name
    and often coordinates; the phone number almost never survives, so the entry
    is marked needs_detail.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(_http_only(req), timeout=15) as r:  # nosec B310
            final = r.geturl()
    except Exception as e:  # noqa: BLE001
        print(f"  couldn't follow that link: {e}")
        final = url

    name = ""
    m = re.search(r"/place/([^/@]+)", final)
    if m:
        name = urllib.parse.unquote_plus(m.group(1)).replace("+", " ").strip()

    coords = ""
    m = re.search(r"@([-\d.]+),([-\d.]+)", final)
    if m:
        coords = f"({m.group(1)}, {m.group(2)})"

    if not name:
        print("  couldn't read a place name from that link.")
        return []

    entry = {"name": name, "source": final, "added": str(date.today()), "needs_detail": True}
    if coords:
        entry["address"] = coords
    return [entry]


# --- 3. the Places API -------------------------------------------------------

def from_places_api(query: str, api_key: str, limit: int = 20, rich: bool = True) -> list[dict]:
    """
    Google Places API (New) Text Search.

    Costs nothing at this volume: the Essentials tier includes 10,000 free
    Text Search requests a month, and building a directory for one small
    business is a few dozen.

    The field mask is not optional -- the API rejects a request without one,
    and asking for fewer fields is what keeps this on the cheap tier.
    """
    body = json.dumps({"textQuery": query, "maxResultCount": min(limit, 20)}).encode()

    # THE FIELD MASK SETS THE PRICE. Places API (New) charges by which fields
    # you ask for, not just by the call. Name and address sit in the cheapest
    # tier; phone numbers, opening hours and accessibility flags move the same
    # request to a dearer one.
    #
    # At the volume this is used for -- a couple of dozen searches to build a
    # directory once -- it is free on every tier, since even the most expensive
    # includes 1,000 free calls a month. The distinction only starts to matter
    # if you ever loop this over hundreds of ZIP codes.
    BASIC = [
        "places.displayName",
        "places.formattedAddress",
        "places.googleMapsUri",
    ]
    FULL = BASIC + [
        "places.nationalPhoneNumber",
        "places.regularOpeningHours.weekdayDescriptions",
        "places.websiteUri",
        "places.businessStatus",
        "places.accessibilityOptions",
    ]
    fields = ",".join(FULL if rich else BASIC)
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": fields,
        },
    )
    try:
        with urllib.request.urlopen(_http_only(req), timeout=30) as r:  # nosec B310
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        sys.exit(f"Google refused the request ({e.code}):\n{detail}\n\n"
                 "Usual causes: the Places API (New) isn't enabled on the project, "
                 "billing isn't set up, or the key is restricted to other APIs.")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"couldn't reach Google: {e}")

    out = []
    for p in data.get("places", []):
        status = p.get("businessStatus", "")
        if status and status != "OPERATIONAL":
            # Don't import somewhere that's closed down. That's the whole
            # failure this is meant to prevent.
            print(f"  skipping {p.get('displayName',{}).get('text','?')} — {status}")
            continue

        entry = {
            "name": p.get("displayName", {}).get("text", ""),
            "source": p.get("googleMapsUri", "Google Places API"),
            "added": str(date.today()),
        }
        if p.get("formattedAddress"):
            entry["address"] = p["formattedAddress"]
        if p.get("nationalPhoneNumber"):
            entry["phone"] = p["nationalPhoneNumber"]
        hours = (p.get("regularOpeningHours") or {}).get("weekdayDescriptions")
        if hours:
            entry["hours"] = "; ".join(hours)
        if p.get("websiteUri"):
            entry["website"] = p["websiteUri"]

        # Accessibility is genuinely load-bearing for this clientele, so it
        # becomes a searchable tag rather than a buried field.
        acc = p.get("accessibilityOptions") or {}
        tags = []
        if acc.get("wheelchairAccessibleEntrance"):
            tags.append("wheelchair_entrance")
        if acc.get("wheelchairAccessibleParking"):
            tags.append("accessible_parking")
        if acc.get("wheelchairAccessibleRestroom"):
            tags.append("accessible_restroom")
        if acc.get("wheelchairAccessibleSeating"):
            tags.append("accessible_seating")
        if tags:
            entry["good_for"] = tags

        out.append(entry)
    return out



# --- bulk: every category, both cities, one command --------------------------

# Query wording matters more than you'd expect. "dialysis" returns articles and
# supply companies; "dialysis center" returns facilities. Each of these was
# chosen to return PLACES YOU CAN DRIVE TO.
BULK_PLAN = [
    ("doctors",           "doctors office"),
    ("dialysis",          "dialysis center"),
    ("physical_therapy",  "physical therapy clinic"),
    ("pharmacies",        "pharmacy"),
    ("hospice",           "hospice care"),
    ("care_homes",        "retirement home"),
    ("grocery",           "grocery store"),
    ("restaurants",       "restaurant"),
    ("parks",             "public park"),
    ("libraries",         "public library"),
    ("senior_centers",    "senior center"),
    ("places_of_worship", "church"),
    ("indoor_summer",     "shopping mall"),
]

CITIES = ["North Las Vegas, NV", "Las Vegas, NV"]


def cmd_bulk(api_key: str, cities: list[str], rich: bool, dry: bool, limit: int) -> None:
    """
    Build the whole directory in one go.

    A warning about what this produces: Google returns the top matches for a
    text query, ranked by ITS idea of prominence. That is not the same as "the
    ones that suit an 80-year-old with a walker". Expect to delete a good
    number by hand -- a 20-result restaurant sweep of Las Vegas will be mostly
    Strip tourist places, which is the opposite of what this business needs.

    Treat the output as a draft to prune, not a finished list. The curated
    entries in places.yaml stay untouched either way.
    """
    total_added = total_skipped = calls = 0

    for section, term in BULK_PLAN:
        for city in cities:
            query = f"{term} in {city}"
            calls += 1
            print(f"\n[{calls:2}] {query}")
            try:
                found = from_places_api(query, api_key, limit=limit, rich=rich)
            except SystemExit as e:
                print(f"     stopped: {e}")
                return
            if not found:
                print("     nothing")
                continue
            if dry:
                for e in found[:5]:
                    print(f"     - {e['name']}")
                if len(found) > 5:
                    print(f"       (+{len(found)-5} more)")
                continue
            added, skipped = add_entries(section, found)
            total_added += added
            total_skipped += skipped
            print(f"     +{added} added, {skipped} already had")

    print(f"\n  {calls} API calls.")
    if dry:
        print("  --dry-run: nothing written.")
        return
    print(f"  {total_added} added to {LOCAL}, {total_skipped} skipped as duplicates.")
    print()
    print("  NOW PRUNE IT. Google ranks by prominence, not by whether a place")
    print("  suits a frail 80-year-old. Open the file, or use:")
    print("      places.cmd list --category restaurants")
    print("      places.cmd remove \"Some Tourist Trap\"")


# --- listing and removing ----------------------------------------------------

def cmd_list(category: str = "") -> None:
    curated, local = load_curated(), load_local()
    sections = [category] if category else sorted(
        set(list(curated) + list(local)) - {"meta", "warnings"}
    )
    for s in sections:
        c = [e for e in curated.get(s, []) if isinstance(e, dict)]
        l = [e for e in local.get(s, []) if isinstance(e, dict)]
        if not c and not l:
            continue
        print(f"\n{s}  ({len(c)} curated, {len(l)} added)")
        for e in c:
            print(f"   .  {e.get('name','')}")
        for e in l:
            flag = "?" if e.get("needs_detail") else "+"
            print(f"   {flag}  {e.get('name','')}   {e.get('address','')}")
    print("\n  .  from places.yaml (hand-checked)")
    print("  +  from places.local.yaml (yours)")
    print("  ?  added but missing an address — fill it in before relying on it")


def cmd_remove(name: str) -> None:
    local = load_local()
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    removed = []
    for section, entries in list(local.items()):
        if not isinstance(entries, list):
            continue
        keep = []
        for e in entries:
            n = re.sub(r"[^a-z0-9]", "", str(e.get("name", "")).lower())
            if want and want in n:
                removed.append(f"{e.get('name')} ({section})")
            else:
                keep.append(e)
        local[section] = keep
    local = {k: v for k, v in local.items() if v}

    if removed:
        save_local(local)
        for r in removed:
            print(f"  removed  {r}")
    else:
        print(f"  nothing in places.local.yaml matches {name!r}")
        if any(want in re.sub(r"[^a-z0-9]", "", str(e.get("name", "")).lower())
               for v in load_curated().values() if isinstance(v, list)
               for e in v if isinstance(e, dict)):
            print("  It IS in places.yaml — edit that file directly in Notepad.")
            print("  (Kept separate on purpose: that file has comments worth not losing.)")


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("takeout", help="import a saved-list CSV exported from Google Maps")
    p.add_argument("path")
    p.add_argument("--category", required=True, choices=KNOWN_SECTIONS)

    p = sub.add_parser("url", help="import one place from a Google Maps link")
    p.add_argument("url")
    p.add_argument("--category", required=True, choices=KNOWN_SECTIONS)

    p = sub.add_parser("search", help="search Google Places (needs an API key)")
    p.add_argument("query")
    p.add_argument("--category", required=True, choices=KNOWN_SECTIONS)
    p.add_argument("--key", default=os.environ.get("GOOGLE_PLACES_API_KEY", ""))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true", help="show what would be added")

    p = sub.add_parser("bulk", help="every category, both cities, one run (needs an API key)")
    p.add_argument("--key", default=os.environ.get("GOOGLE_PLACES_API_KEY", ""))
    p.add_argument("--cities", nargs="+", default=CITIES)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--basic", action="store_true",
                   help="name and address only - cheapest tier, no phone or hours")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("list", help="show everything")
    p.add_argument("--category", default="")

    p = sub.add_parser("remove", help="remove a place you added")
    p.add_argument("name")

    a = ap.parse_args()

    if a.cmd == "bulk":
        if not a.key:
            sys.exit("No API key. Set GOOGLE_PLACES_API_KEY in .env, or pass --key.")
        cmd_bulk(a.key, a.cities, rich=not a.basic, dry=a.dry_run, limit=a.limit)
        return 0
    if a.cmd == "list":
        cmd_list(a.category)
        return 0
    if a.cmd == "remove":
        cmd_remove(a.name)
        return 0

    if a.cmd == "takeout":
        path = Path(a.path)
        if not path.exists():
            sys.exit(f"no such file: {path}")
        found = from_takeout(path, a.category)
    elif a.cmd == "url":
        found = from_url(a.url)
    else:
        if not a.key:
            sys.exit(
                "No API key.\n\n"
                "Either pass --key, or set GOOGLE_PLACES_API_KEY in .env.\n"
                "console.cloud.google.com -> enable 'Places API (New)' -> create a key.\n"
                "10,000 Text Searches a month are free; this uses a few dozen.\n\n"
                "Don't want a key? Star the places in Google Maps on your phone,\n"
                "export the list, and use:  places.cmd takeout <file> --category ..."
            )
        found = from_places_api(a.query, a.key, a.limit)

    if not found:
        print("  nothing found.")
        return 1

    print(f"\n  found {len(found)}:")
    for e in found:
        flag = " (no address)" if e.get("needs_detail") else ""
        print(f"    {e['name']}{flag}")
        if e.get("address"):
            print(f"        {e['address']}")
        if e.get("phone"):
            # 2026-11-20: false positive for "sensitive data". This is a business's public phone number from
            # Places/Takeout, shown so you can check what is about to be saved; the API key is never printed.
            print(f"        {e['phone']}")

    if getattr(a, "dry_run", False):
        print("\n  --dry-run: nothing written.")
        return 0

    added, skipped = add_entries(a.category, found)
    print(f"\n  added {added} to {LOCAL}")
    if skipped:
        print(f"  skipped {skipped} already on the list")
    if any(e.get("needs_detail") for e in found):
        print("  Some have no address — open the file and fill them in, or they")
        print("  can't be used for directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
