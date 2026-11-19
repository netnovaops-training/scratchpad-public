"""Give Robin's wiki a sidebar, so the pages are findable by browsing.

483 plant pages and 200-odd genus pages are useless if the only way in is
the search box -- somebody who does not already know what a plant is called
has nowhere to start. The sidebar is what turns a pile of pages into an
encyclopedia you can walk into.

THE SHAPE FOLLOWS HOW SOMEBODY ACTUALLY ARRIVES: "what is this plant" (start
at the genus, narrow to the cultivar), or "what is in that bed" (start at the
list). Both doors are at the top; the garden's live tools are below them,
because a wiki page is where you read and the site is where you do.

VISIBILITY IS LEFT ALONE. Wiki.js's own groups already decide who sees what
-- public read, signed-in editors, Robin and Jason administering -- and
re-stating any of that here would be a second, quieter copy of the
permission model that could drift from the real one.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_env():
    for name in (os.path.join(os.path.dirname(HERE), ".env"),
                 "/seed/wiki.env", "/app/wiki.env"):
        if not os.path.exists(name):
            continue
        with open(name, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("WIKIJS_") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))


load_env()
import wikijs

SITE = "https://garden.example.org"

SAVE_NAV = """
mutation ($tree: [NavigationTreeInput]!) {
  navigation { updateTree(tree: $tree) {
    responseResult { succeeded errorCode message } } } }
"""


def link(nid, label, target, external=False, icon="mdi-leaf"):
    return {
        "id": nid, "kind": "link", "label": label, "icon": icon,
        "targetType": "external" if external else "page",
        "target": target if external else "/en/" + target.lstrip("/"),
        "visibilityMode": "all", "visibilityGroups": [],
    }


def header(nid, label):
    return {"id": nid, "kind": "header", "label": label, "icon": "",
            "targetType": "external", "target": "",
            "visibilityMode": "all", "visibilityGroups": []}


ITEMS = [
    link("home", "Home", "home", icon="mdi-home"),
    header("h-browse", "Browse"),
    link("browse", "Browse by anything", "browse", icon="mdi-compass"),
    link("genus", "Every genus", "genus", icon="mdi-family-tree"),
    link("plants", "Every plant", "plants", icon="mdi-format-list-bulleted"),
    header("h-safety", "Safety"),
    link("tox", "Toxic to dogs and cats",
         "by/toxicity/toxic-to-dogs-cats", icon="mdi-dog"),
    link("toxp", "Toxic to people and pets",
         "by/toxicity/toxic-to-humans-pets", icon="mdi-alert"),
    link("edible", "Edible", "by/fruit-edibility/edible", icon="mdi-food-apple"),
    header("h-garden", "The garden itself"),
    link("cat", "Plant catalogue", SITE + "/catalog.php", True,
         icon="mdi-database"),
    link("beds", "The beds", SITE + "/beds", True, icon="mdi-vector-square"),
    link("diag", "Identify a plant", SITE + "/diagnose", True,
         icon="mdi-camera"),
    link("tag", "Scan a tag", SITE + "/tagscan", True, icon="mdi-tag"),
    link("spend", "Plant spending", SITE + "/spend", True,
         icon="mdi-receipt"),
    # THE APPROVAL QUEUE BELONGS IN THE SIDEBAR, not only in a chat reply.
    # A queue you have to be told about is a queue that gets forgotten; one
    # that is always visible gets emptied.
    link("queue", "Waiting for approval", SITE + "/wiki-queue", True,
         icon="mdi-inbox-arrow-down"),
]


def main():
    bad = wikijs.check()
    if bad:
        print("not configured:", bad)
        return 1
    data, err = wikijs.gql(SAVE_NAV, {"tree": [{"locale": "en",
                                                "items": ITEMS}]})
    if err:
        print("FAILED:", err)
        return 1
    res = (((data or {}).get("navigation") or {}).get("updateTree") or {}
           ).get("responseResult") or {}
    if not res.get("succeeded"):
        print("refused:", res.get("message") or res)
        return 1
    print(f"sidebar set: {len(ITEMS)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
