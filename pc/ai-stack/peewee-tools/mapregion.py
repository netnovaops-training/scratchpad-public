#!/usr/bin/env python3
# =============================================================================
#  mapregion.py -- the ground Pee-Wee worked (2026-09-01, Jason).
#
#  One map covering BOTH cities: downtown Albany (WABY, the Ten Eyck on
#  South Pearl Street) and Troy (WXKW, where he was music director). Roads
#  across the whole corridor, building footprints in the two downtown cores
#  only -- broad detail, small file, and vector so the tour can zoom into
#  any block without blurring.
#
#  Every marker is a documented address (see PLACES below): the Radio
#  Centre at 8 Elk Street, the Hendrick Hudson Hotel in Troy, South Pearl
#  Street from the family's own photograph, and his grave in the Garden
#  of Prayer -- that one exact to the plot's own GPS.
#
#  Writes intro/albany-region.svg (the picture) and data/places.json (the
#  projected coordinates the tour flies between).
# =============================================================================
import json
import os
import urllib.parse
import urllib.request
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


SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))
UA = {"User-Agent": "PeeWeeTribute/1.0 (family memorial; user@example.com)"}
OVERPASS = "https://overpass-api.de/api/interpreter"

# The corridor, framed to the tribute's 1000x700 viewBox.
SOUTH, WEST, NORTH, EAST = 42.625, -73.900, 42.779, -73.600
VW, VH = 1000.0, 700.0

# The two cores that get buildings.
CORES = [(42.6395, -73.7595, 42.6560, -73.7405),    # downtown Albany
         (42.7230, -73.6990, 42.7350, -73.6850)]    # downtown Troy

# Every location below is DOCUMENTED, and the card says only what the
# record says:
#   WABY  -- the Radio Centre at 8 Elk Street housed WOKO and WABY's
#            studios (Knickerbocker News, 5 May 1939). He began there in
#            1955 and stayed twenty-two years.
#   WXKW  -- studios in the Hendrick Hudson Hotel, downtown Troy;
#            licensed to Watervliet, on air 1962-1966 (Wikipedia).
#   Ten Eyck -- South Pearl Street, from the family's own photograph.
#   Evergreen -- his grave, Garden of Prayer; GPS from the Find a Grave
#            memorial, exact to the plot.
PLACES = [
    {"key": "waby", "lat": 42.6528, "lon": -73.7573,
     "name": "WABY 1400", "where": "Radio Centre, 8 Elk Street, Albany",
     "line": "On the air here from 1955, twenty-two years"},
    {"key": "wxkw", "lat": 42.7297, "lon": -73.6917,
     "name": "WXKW", "where": "Hendrick Hudson Hotel, downtown Troy",
     "line": "Music director, 1965"},
    {"key": "teneyck", "lat": 42.6455, "lon": -73.7525,
     "name": "Ten Eyck Record Shop", "where": "South Pearl Street, Albany",
     "line": "Where the city came for its forty-fives"},
    {"key": "evergreen", "lat": 42.75713, "lon": -73.88072,
     "name": "Evergreen Memorial Park", "where": "Colonie",
     "line": "Garden of Prayer, where he rests"},
]


def q(body):
    data = urllib.parse.urlencode({"data": body}).encode()
    req = urllib.request.Request(OVERPASS, data=data, headers=UA)
    with urllib.request.urlopen(_http_only(req), timeout=240) as r:  # nosec B310
        return json.loads(r.read())


def project(lat, lon):
    return ((lon - WEST) / (EAST - WEST) * VW,
            (NORTH - lat) / (NORTH - SOUTH) * VH)


def simplify(pts, tol=0.8):
    out = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    out.append(pts[-1])
    return out


def path(pts, close=False):
    return ('M' + 'L'.join(f'{round(x)} {round(y)}' for x, y in pts)
            + ('Z' if close else ''))


def main():
    bbox = f"{SOUTH},{WEST},{NORTH},{EAST}"
    core_q = "".join(
        f'way["building"]({s},{w},{n},{e});' for s, w, n, e in CORES)
    print("asking OpenStreetMap for Albany and Troy...", flush=True)
    res = q(f"""[out:json][timeout:200];
    (
      way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified)$"]({bbox});
      {core_q}
    );
    out geom;""")

    majors, streets, buildings = [], [], []
    BIG = {"motorway", "trunk", "primary", "secondary"}
    for el in res.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [project(g["lat"], g["lon"]) for g in geom]
        pts = [(x, y) for x, y in pts
               if -80 <= x <= VW + 80 and -80 <= y <= VH + 80]
        if len(pts) < 2:
            continue
        pts = simplify(pts)
        tags = el.get("tags", {})
        if "building" in tags:
            if len(pts) >= 3:
                buildings.append(pts)
        else:
            (majors if tags.get("highway") in BIG else streets).append(pts)

    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700">',
           ('<g fill="#9c7a3c" fill-opacity=".15" stroke="#9c7a3c" '
           'stroke-width=".5" opacity=".6">'),
           ''.join(f'<path d="{path(p, True)}"/>' for p in buildings),
           '</g><g stroke="#9c7a3c" stroke-width=".7" opacity=".4" fill="none">',
           ''.join(f'<path d="{path(p)}"/>' for p in streets),
           '</g><g stroke="#c9a36a" stroke-width="1.8" opacity=".6" fill="none">',
           ''.join(f'<path d="{path(p)}"/>' for p in majors),
           '</g></svg>']
    out = os.path.join(SITE, "intro", "albany-region.svg")
    Path(out).write_text(''.join(svg), encoding="utf-8", newline="\n")

    places = []
    for p in PLACES:
        x, y = project(p["lat"], p["lon"])
        places.append({**{k: p[k] for k in ("key", "name", "where", "line")},
                       "x": round(x, 1), "y": round(y, 1)})
    Path(os.path.join(SITE, "data", "places.json")).write_text(json.dumps({"_note": "projected to the 1000x700 map; every address "
                             "is documented -- see mapregion.py for the "
                             "source behind each one",
                    "places": places}, indent=1), encoding="utf-8", newline="\n")
    print(f"{len(majors)} through-roads, {len(streets)} streets, "
          f"{len(buildings)} buildings -> {os.path.getsize(out)//1024} KB")
    for p in places:
        print(f"   {p['name']:24} ({p['x']}, {p['y']})")


if __name__ == "__main__":
    main()
