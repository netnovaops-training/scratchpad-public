#!/usr/bin/env python3
# =============================================================================
#  mapdata.py -- the real Albany (2026-09-01, Jason: "just get map data of
#  downtown Albany around South Pearl Street... a lot of little fine roads
#  and buildings").
#
#  Pulls the actual street network and building footprints from
#  OpenStreetMap (Overpass API, open data, ODbL) for the blocks around
#  South Pearl Street, projects them to the tribute's SVG viewBox, and
#  writes data/albany.json for the intro map to draw.
#
#  The streets around downtown Albany are essentially the same today as
#  they were in his time, which is what makes this honest: the geography
#  is real even where the photographs are gone.
# =============================================================================
import json
import os
import urllib.parse
import urllib.request


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))
UA = {"User-Agent": "PeeWeeTribute/1.0 (family memorial; user@example.com)"}

# The frame: downtown Albany, centred on the South Pearl Street blocks.
SOUTH, WEST, NORTH, EAST = 42.6395, -73.7595, 42.6560, -73.7405
OVERPASS = "https://overpass-api.de/api/interpreter"

# Where the tribute's SVG lives: 1000x700 viewBox.
VW, VH = 1000.0, 700.0


def q(body):
    data = urllib.parse.urlencode({"data": body}).encode()
    req = urllib.request.Request(OVERPASS, data=data, headers=UA)
    with urllib.request.urlopen(_http_only(req), timeout=180) as r:  # nosec B310
        return json.loads(r.read())


def project(lat, lon):
    x = (lon - WEST) / (EAST - WEST) * VW
    y = (NORTH - lat) / (NORTH - SOUTH) * VH
    return round(x, 1), round(y, 1)


def main():
    bbox = f"{SOUTH},{WEST},{NORTH},{EAST}"
    print("asking OpenStreetMap for downtown Albany...", flush=True)
    res = q(f"""[out:json][timeout:120];
    (
      way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street|pedestrian|footway)$"]({bbox});
      way["building"]({bbox});
    );
    out geom;""")

    streets, majors, buildings = [], [], []
    BIG = {"motorway", "trunk", "primary", "secondary"}
    for el in res.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [project(g["lat"], g["lon"]) for g in geom]
        # keep it inside the frame
        pts = [(x, y) for x, y in pts if -50 <= x <= VW + 50 and -50 <= y <= VH + 50]
        if len(pts) < 2:
            continue
        tags = el.get("tags", {})
        if "building" in tags:
            if len(pts) >= 3:
                buildings.append(pts)
        else:
            (majors if tags.get("highway") in BIG else streets).append(pts)

    doc = {
        "_note": "OpenStreetMap (ODbL) -- downtown Albany around South Pearl "
                 "Street, projected to the tribute's 1000x700 viewBox. The "
                 "grid is essentially unchanged since Pee-Wee's time.",
        "bbox": [SOUTH, WEST, NORTH, EAST],
        "majors": majors, "streets": streets, "buildings": buildings,
    }
    out = os.path.join(SITE, "data", "albany.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{len(majors)} major roads, {len(streets)} streets, "
          f"{len(buildings)} buildings -> data/albany.json "
          f"({os.path.getsize(out)//1024} KB)")


if __name__ == "__main__":
    main()
