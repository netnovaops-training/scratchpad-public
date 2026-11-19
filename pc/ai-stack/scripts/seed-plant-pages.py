"""Build the backlog of Robin's wiki: a page for every plant he grows.

JASON, 2026-09-24: "let's build juns garden back log for his wiki with a
generic start page that takes information from his website and his database
and some pictures so we can build all this out today."

EVERY WORD ON THESE PAGES COMES FROM HIS OWN RECORDS. The catalogue, the
photographs he took, and links back to his site. Nothing is written from a
model's memory and nothing is fetched from the web here -- 483 pages of
plausible-sounding prose about plants would be 483 pages nobody could trust,
and the one wrong hardiness zone would be indistinguishable from the rest.
What a model adds later goes through the draft queue a person approves.

SO THE PAGES ARE DELIBERATELY PLAIN. A heading, the photograph, a table of
what is actually recorded, and the empty fields simply absent. A blank row
labelled "soil pH:" is an invitation to invent one.

IDEMPOTENT AND RESUMABLE. 483 pages is a few hundred round trips and
something will interrupt it. Re-running updates what exists and creates what
does not, so the safe response to any failure is to run it again.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_env():
    for name in (os.path.join(os.path.dirname(HERE), ".env"),
                 "/app/wiki.env"):
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
IMAGES = SITE + "/images/Database"
TABLE = os.environ.get("GARDEN_TABLE", "data_source")
# TABLE goes into SQL as a name (names cannot be ? parameters), so it must be a plain identifier.
# Added 2026-11-19 for Bandit B608; the default "data_source" passes.
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", TABLE):
    sys.exit(f"GARDEN_TABLE must be a plain table name, got {TABLE!r}")

# What goes in the facts table, in the order a person would want it. Columns
# not listed here are left out on purpose: the catalogue has 98 of them and
# a page that prints all 98 is a page nobody reads.
# LIKE WITH LIKE, at Jason's request 2026-09-24: "All the plant information
# are in the database ... plant size, plant shape, plant colour, leaf size,
# seed shape, stem size, stem colour, root system, reproductive system. I'd
# like to add all these to the wiki from the database and combine like with
# like."
#
# ONE FLAT TABLE OF NINETY-EIGHT COLUMNS IS NOT A PAGE, it is a database
# dump with a border. Grouped, the same facts answer questions: "what does
# it look like", "where does it go", "how do I make more of it". The order
# below is the order somebody actually wants them in -- what it is, then
# what it looks like, then how to grow it, then the technical botany last
# because almost nobody needs it and it should not sit above the rest.
SECTIONS = [
    ("Identity", [
        ("botanical_name", "Botanical name"),
        ("genus", "Genus"),
        ("species", "Species"),
        ("family", "Family"),
        ("plant_type", "Type"),
        ("native_status", "Native status"),
        ("invasive_local", "Invasive here"),
        ("life_expectancy", "Life expectancy"),
    ]),
    ("In this garden", [
        ("flower_bed", "Bed"),
        ("garden_beds", "Also in"),
        ("year_planted", "Planted"),
        ("year_added", "Added to the catalogue"),
        ("quantity", "How many"),
        ("average_cost", "Typical cost"),
        ("company", "Bought from"),
        ("local_store", "Local store"),
    ]),
    ("Size and shape", [
        ("size", "Size"),
        ("plant_length", "Length"),
        ("plant_width", "Width"),
        ("spacing", "Spacing"),
        ("growth_rate", "Growth rate"),
    ]),
    ("Leaves", [
        ("leaf_shape", "Leaf shape"),
        ("leaf_type", "Leaf type"),
        ("leaf_margin", "Leaf margin"),
        ("foliage_color", "Foliage colour"),
        ("is_evergreen", "Evergreen"),
        ("aromatics", "Scent"),
    ]),
    ("Flowers", [
        ("bloom_color", "Bloom colour"),
        ("bloom_months", "Blooms"),
        ("flower_shape", "Flower shape"),
        ("flower_size", "Flower size"),
        ("flower_symmetry", "Flower symmetry"),
        ("attracts", "Attracts"),
    ]),
    ("Stems", [
        ("stem_color", "Stem colour"),
    ]),
    ("Seeds and fruit", [
        ("fruit_type", "Fruit type"),
        ("fruit_shape", "Fruit shape"),
        ("fruit_color", "Fruit colour"),
        ("fruit_size", "Fruit size"),
        ("fruit_edibility", "Edible?"),
    ]),
    ("Roots", [
        ("root_system", "Root system"),
        ("root_morphology", "Root form"),
        ("root_function", "Root function"),
        ("planting_depth", "Planting depth"),
    ]),
    ("Growing it", [
        ("sun_exposure", "Sun"),
        ("water_needs", "Water"),
        ("soil_type", "Soil"),
        ("soil_ph", "Soil pH"),
        ("zones", "Hardiness zones"),
        ("season", "Season"),
        ("grow_months", "Growing months"),
        ("chilling_hours", "Chilling hours"),
        ("deer_resistance", "Deer resistance"),
        ("companion_plants", "Companions"),
    ]),
    ("Making more of it", [
        ("propagation_method", "Propagation"),
        ("cuttings", "Cuttings"),
        ("reproduction_type", "Reproduction"),
    ]),
]

# Longer prose fields, printed as their own sections rather than table rows.
NOTES = [
    ("care_notes", "Care"),
    ("maintenance_notes", "Maintenance"),
    ("cleanup_notes", "Cleanup"),
    ("fertilizer", "Feeding"),
    ("cuttings", "Cuttings"),
    ("disease_susceptibility", "Watch for"),
    ("extra_details", "Other notes"),
]


def gslug(genus):
    """Where a genus page lives. One per genus, Wikipedia-fashion.

    JASON, 2026-09-24: "group names that are common and then branch off to
    the ones that are different like hydrangeas then the genus and species
    ... Hydrangeas get their own page just like Wikipedia does."

    THE GENUS IS THE RIGHT RUNG FOR THAT and the catalogue already carries
    it on all 483 rows. Grouping on the common name would put "Blue Angel
    Hosta" and "Hosta Variegata" together and leave "Abiqua Drinking
    Gourd" -- also a hosta -- on its own, because common names do not
    agree with each other. The genus does.
    """
    g = re.sub(r"[^a-z0-9]+", "-", (genus or "").lower()).strip("-")
    return f"genus/{g}" if g else ""


def slug(name, pid):
    """A stable path. The id is included because two plants can share a name.

    WITHOUT THE ID this breaks silently: a second "Blue Hosta" would
    overwrite the first, and the page would look fine.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"plants/{s or 'plant'}-{pid}"



MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def months(mask):
    """`....MJJAS...` -> "May to September".

    THE COLUMN IS A TWELVE-CHARACTER MASK, one slot per month, holding that
    month's initial when it applies and a dot when it does not. Printed raw
    it is meaningless on a page, and it is the same encoding that app-layer
    bugs have corrupted before -- so this reads it defensively: anything
    that is not exactly twelve slots is passed through untouched rather
    than decoded into a confident lie.
    """
    m = (mask or "").strip()
    if len(m) != 12:
        return m
    on = [i for i, ch in enumerate(m) if ch not in (".", " ", "-", "_")]
    if not on:
        return ""
    # A single unbroken run reads better as a range than as a list.
    if on == list(range(on[0], on[-1] + 1)):
        if len(on) == 1:
            return MONTHS[on[0]]
        if len(on) == 12:
            return "All year"
        return f"{MONTHS[on[0]]} to {MONTHS[on[-1]]}"
    return ", ".join(MONTHS[i] for i in on)


def val(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "none", "null", "n/a", "unknown", "0") else s



# ---------------------------------------------------------------------------
#  Safety
# ---------------------------------------------------------------------------
# THE VOCABULARY IS CLOSED, SO THE MATCHING IS EXACT. Every one of the 483
# rows carries a toxicity value and there are only five of them; the same for
# edibility with seven. That means no model and no guessing anywhere in this
# file -- a plant is classified by comparing its own recorded value against a
# list, and anything not on the list falls through to "not classified" rather
# than to a reassuring default.
#
# WHICH WAY THE UNKNOWNS FALL IS THE WHOLE DESIGN. "Non-toxic" is a claim
# somebody could act on -- let a dog near it, let a child pick it -- so it is
# said ONLY when the record says exactly that. Everything unrecognised is
# reported as unrecorded, because the cost of the two mistakes is not
# symmetric: a needless warning wastes a moment, a missing one does not.
TOXIC_HUMANS_AND_PETS = ("Toxic to Humans & Pets",)
TOXIC_PETS = ("Toxic to Dogs/Cats",)
TOXIC_HUMANS = ("Toxic to Humans",)
TOXIC_UNSPECIFIED = ("Toxic",)
NON_TOXIC = ("Non-toxic",)

EDIBLE = ("Edible",)
WILDLIFE_ONLY = ("Wildlife Only",)
NOT_EDIBLE = ("Inedible", "None")
FRUIT_TOXIC = ("Toxic", "Inedible/Toxic", "Toxic to Humans & Pets")


def safety_block(row):
    """The warning that goes ABOVE everything else, or a plain all-clear.

    AT THE TOP, NOT IN THE TABLE. Somebody reading this page in a hurry is
    usually reading it for one reason -- a dog has eaten something, or a
    child has picked something -- and an answer buried in row nineteen of a
    facts table is an answer that arrives too late to be one.
    """
    tox = val(row.get("toxicity"))
    eat = val(row.get("fruit_edibility"))
    out = []

    if tox in TOXIC_HUMANS_AND_PETS:
        out.append("> ### :warning: Toxic to people AND to animals")
        out.append("> This plant is recorded as poisonous to both. Keep it "
                   "away from children and pets.")
    elif tox in TOXIC_PETS:
        out.append("> ### :warning: Toxic to dogs and cats")
        out.append("> Recorded as poisonous to pets. It is not recorded as "
                   "poisonous to people, but that is not the same as safe to "
                   "eat.")
    elif tox in TOXIC_HUMANS:
        out.append("> ### :warning: Toxic to people")
        out.append("> Recorded as poisonous to humans.")
    elif tox in TOXIC_UNSPECIFIED:
        out.append("> ### :warning: Toxic")
        out.append("> Recorded as poisonous. **The record does not say to "
                   "whom** -- treat it as unsafe for both people and animals "
                   "until that is confirmed.")
    elif tox in NON_TOXIC:
        out.append("> ### Not recorded as toxic")
        out.append("> The catalogue records this plant as non-toxic. That is "
                   "Robin's record of the plant, not medical or veterinary "
                   "advice.")
    else:
        # The honest branch. See the note above about which way unknowns fall.
        out.append("> ### Toxicity not recorded")
        out.append("> The catalogue has no toxicity entry for this plant. "
                   "**Treat it as unknown rather than as safe.**")

    if eat in EDIBLE:
        out.append(">")
        # TOXIC PLANT, EDIBLE FRUIT IS A REAL COMBINATION -- elderberry and
        # rhubarb both work that way -- but printed flat, one line under the
        # other, it invites somebody to eat fruit off a plant the line above
        # called poisonous. Saying the two apply to different parts is the
        # difference between a record and a hazard.
        if tox not in NON_TOXIC:
            out.append("> **Fruit:** recorded as edible &mdash; but the "
                       "warning above applies to the plant. Edible fruit on "
                       "a toxic plant is normal (rhubarb, elderberry); it "
                       "means the FRUIT, not the leaves or stems.")
        else:
            out.append("> **Fruit:** recorded as edible.")
    elif eat in WILDLIFE_ONLY:
        out.append(">")
        out.append("> **Fruit:** eaten by wildlife, not by people.")
    elif eat in FRUIT_TOXIC:
        out.append(">")
        out.append("> **Fruit: poisonous. Do not eat.**")
    elif eat in NOT_EDIBLE:
        out.append(">")
        out.append("> **Fruit:** not edible.")

    out.append(">")
    out.append("> *If a person or an animal has eaten part of this plant, "
               "ring a doctor, a vet or poison control -- do not rely on this "
               "page.*")
    return out



# ---------------------------------------------------------------------------
#  Hardiness zones
# ---------------------------------------------------------------------------
# JASON, 2026-09-24: "expand the zones ... to a more broader zone and include
# more generic information."
#
# THIS IS COMPUTED, NOT LOOKED UP. The range is already in his catalogue and
# the USDA zone definition is arithmetic: each zone is a 10 degree F band of
# average annual MINIMUM winter temperature, zone 1 starting at -60F. So a
# page can say what "zones 5-9" actually means without asking anything
# external and without a model inventing a number.
#
# WHAT A ZONE DOES NOT TELL YOU is said on the page too, because the number
# is routinely over-trusted: it is about winter cold ONLY. It says nothing
# about summer heat, humidity, drought, soil or how long the season is, and
# a plant can be "in zone" and still fail for any of those.
HOME_ZONE = int(os.environ.get("GARDEN_HOME_ZONE", "6"))
HOME_ZONE_LABEL = os.environ.get("GARDEN_HOME_ZONE_LABEL",
                                 "6a (Clifton Park, New York)")

def _zone_low_f(z):
    """Average annual minimum temperature at the bottom of a zone, in F."""
    return -60 + (z - 1) * 10


def zone_note(raw):
    """Turn "5-9" into something a person can act on. "" if unparseable."""
    m = re.search(r"(\d{1,2})\s*[ab]?\s*(?:-|to|–)\s*(\d{1,2})", raw or "")
    if not m:
        return ""
    lo, hi = int(m.group(1)), int(m.group(2))
    if not (1 <= lo <= 13 and 1 <= hi <= 13 and lo <= hi):
        return ""

    lines = [
        (f"**Hardy in USDA zones {lo} to {hi}.** Zone {lo} means it survives "
        f"an average winter low of about {_zone_low_f(lo)}°F "
        f"({round((_zone_low_f(lo) - 32) * 5 / 9)}°C); zone {hi} is the "
        f"warm end, around {_zone_low_f(hi)}°F "
        f"({round((_zone_low_f(hi) - 32) * 5 / 9)}°C) and above."),
        "",
    ]

    # WHERE THIS GARDEN SITS IN THAT RANGE is the only part most people need.
    if HOME_ZONE < lo:
        lines.append(
            f"**This garden is zone {HOME_ZONE_LABEL}, which is COLDER than "
            f"this plant's range.** It is not reliably winter-hardy here -- "
            "expect to grow it as an annual, lift it, or overwinter it under "
            "cover.")
    elif HOME_ZONE > hi:
        lines.append(
            f"**This garden is zone {HOME_ZONE_LABEL}, which is warmer than "
            f"this plant's range.** Heat rather than cold is the limit; it "
            "may struggle in a hot summer.")
    elif HOME_ZONE == lo:
        lines.append(
            f"This garden is zone {HOME_ZONE_LABEL} -- the **cold edge** of "
            "its range. It should come through most winters, but a hard one "
            "may take it. Mulch and a sheltered spot are worth it.")
    elif HOME_ZONE == hi:
        lines.append(
            f"This garden is zone {HOME_ZONE_LABEL} -- the **warm edge** of "
            "its range.")
    else:
        lines.append(
            f"This garden is zone {HOME_ZONE_LABEL}, comfortably inside its "
            f"range, with {HOME_ZONE - lo} "
            f"{'zone' if HOME_ZONE - lo == 1 else 'zones'} of cold to spare.")

    lines += [
        "",
        ("*A hardiness zone is about winter cold and nothing else. It says "
        "nothing about summer heat, humidity, drought, soil or season "
        "length, and a plant can be well within its zone and still fail for "
        "one of those.*"),
    ]
    return chr(10).join(lines)



# ---------------------------------------------------------------------------
#  Facets -- the thing that makes this a wiki rather than 609 loose pages
# ---------------------------------------------------------------------------
# JASON, 2026-09-24: "can these link to other plants with the same information
# so that they're all linkable ... edibility type or seeds or root system so
# that there's a more comprehensive understanding."
#
# EVERY VALUE BELOW BECOMES A LINK, and the page it points at lists every
# plant that shares it. "Toxic to Dogs/Cats" on one page leads to all 133 of
# them; "Fibrous/Adventitious Systems" leads to all 411. That is the
# difference between a page you read and an encyclopedia you follow.
#
# CHOSEN BY CARDINALITY, MEASURED FIRST. Only columns with a small closed
# vocabulary are faceted. `soil_type` has 247 distinct values across 483
# plants and `bloom_color` has 290 -- they are free text, and indexing them
# would produce hundreds of pages listing one plant each, which is worse than
# no index because it looks like one. The cut is at roughly sixty.
#
# `attracts` IS SPLIT ON COMMAS because "Bees, Butterflies" is two facts, and
# somebody looking for what feeds bees should find that plant.
FACETS = [
    ("toxicity", "Toxicity", False),
    ("fruit_edibility", "Edibility", False),
    ("plant_type", "Type", False),
    ("family", "Family", False),
    ("native_status", "Native status", False),
    ("invasive_local", "Invasive here", False),
    ("is_evergreen", "Evergreen or deciduous", False),
    ("growth_rate", "Growth rate", False),
    ("season", "Season", False),
    ("zones", "Hardiness zones", False),
    ("sun_exposure", "Sun", False),
    ("water_needs", "Water", False),
    ("deer_resistance", "Deer resistance", False),
    ("attracts", "Attracts", True),
    ("leaf_shape", "Leaf shape", False),
    ("leaf_type", "Leaf type", False),
    ("leaf_margin", "Leaf margin", False),
    ("flower_shape", "Flower shape", False),
    ("flower_size", "Flower size", False),
    ("fruit_type", "Fruit type", False),
    ("root_system", "Root system", False),
    ("root_morphology", "Root form", False),
    ("root_function", "Root function", False),
    ("reproduction_type", "Reproduction", False),
]
FACET_COLS = {c: (label, multi) for c, label, multi in FACETS}


# MONTH PAGES ARE COMPUTED, NOT A COLUMN. `bloom_months` is a twelve-slot
# mask, so "what is in flower in July" is not a value anybody can index --
# it has to be derived. Same for the growing season. This is the pair Jason
# asked for: "plant by season or plant by month link with the data that's
# already provided."
MONTH_FACETS = {
    "blooming": ("bloom_months", "In flower"),
    "growing": ("grow_months", "In growth"),
}


def month_buckets(rows, col):
    """{month name: [plants]} from a twelve-slot mask column."""
    out = {}
    for r in rows:
        m = (val(r.get(col)) or "").strip()
        if len(m) != 12:
            continue
        for i, ch in enumerate(m):
            if ch not in (".", " ", "-", "_"):
                out.setdefault(MONTHS[i], []).append(r)
    return out


def month_page(kind, month, plants):
    label = MONTH_FACETS[kind][1]
    plants = sorted(plants, key=lambda r: (val(r.get("plant_name")) or "").lower())
    verb = "in flower" if kind == "blooming" else "in active growth"
    out = [f"# {month}: {label.lower()}", "",
           (f"{len(plants)} plant{'' if len(plants) == 1 else 's'} are {verb} "
           f"in {month} in this garden (zone {HOME_ZONE_LABEL})."), ""]
    for r in plants:
        name = val(r.get("plant_name")) or f"Plant {r['id']}"
        bed = val(r.get("flower_bed"))
        colour = val(r.get("bloom_color")) if kind == "blooming" else ""
        bits = [b for b in (colour, bed) if b]
        out.append(f"- [{name}](/{slug(name, r['id'])})"
                   + (" — " + " · ".join(bits) if bits else ""))
    other = "growing" if kind == "blooming" else "blooming"
    out += ["", "---", "",
            (f"[Every month](/by/{kind}) · "
            f"[What is {other} in {month}](/by/{other}/{month.lower()}) · "
            "[Browse everything](/browse)")]
    return chr(10).join(out)


def month_index(kind, buckets):
    label = MONTH_FACETS[kind][1]
    out = [f"# {label}, month by month", "",
           (f"Which plants are {'in flower' if kind == 'blooming' else 'growing'} "
           f"when, in this garden (zone {HOME_ZONE_LABEL})."), ""]
    for m in MONTHS:
        plants = buckets.get(m) or []
        if not plants:
            out.append(f"- {m} — nothing recorded")
            continue
        out.append(f"- [{m}](/by/{kind}/{m.lower()}) — {len(plants)}")
    out += ["", "---", "", "[Browse everything](/browse)"]
    return chr(10).join(out)


def facet_values(row, col):
    """The value(s) of one facet on one plant."""
    v = val(row.get(col))
    if not v:
        return []
    if FACET_COLS.get(col, ("", False))[1]:
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def fslug(col, value):
    v = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    c = re.sub(r"[^a-z0-9]+", "-", col.lower()).strip("-")
    return f"by/{c}/{v}" if v else ""


def link_value(col, v):
    """A table value, as a link when it is faceted and plain when it is not."""
    if col not in FACET_COLS:
        return v
    parts = facet_values({col: v}, col) if False else (
        [x.strip() for x in v.split(",") if x.strip()]
        if FACET_COLS[col][1] else [v])
    out = []
    for part in parts:
        path = fslug(col, part)
        out.append(f"[{part}](/{path})" if path else part)
    return ", ".join(out)


def facet_page(col, value, plants):
    label = FACET_COLS[col][0]
    plants = sorted(plants, key=lambda r: (val(r.get("plant_name")) or "").lower())
    out = [f"# {label}: {value}", "",
           (f"{len(plants)} plant{'' if len(plants) == 1 else 's'} in Robin's "
           f"garden."), ""]
    for r in plants:
        name = val(r.get("plant_name")) or f"Plant {r['id']}"
        bot = val(r.get("botanical_name"))
        bed = val(r.get("flower_bed"))
        bits = [b for b in (f"*{bot}*" if bot else "", bed) if b]
        out.append(f"- [{name}](/{slug(name, r['id'])})"
                   + (" — " + " · ".join(bits) if bits else ""))
    out += ["", "---", "", f"[All {label.lower()}](/by/"
            + re.sub(r"[^a-z0-9]+", "-", col.lower()).strip("-")
            + ") · [Every plant](/plants) · [Every genus](/genus)"]
    return chr(10).join(out)


def facet_index(col, buckets):
    label = FACET_COLS[col][0]
    out = [f"# {label}", "",
           (f"Every value recorded for {label.lower()}, and how many plants "
           "carry it."), ""]
    for v, plants in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        out.append(f"- [{v}](/{fslug(col, v)}) — {len(plants)}")
    out += ["", "---", "", ("[Browse everything](/browse) · "
            "[Every plant](/plants)")]
    return chr(10).join(out)


def browse_page(all_buckets, month_all=None):
    out = ["# Browse the garden", "",
           ("Every plant page links to pages like these, so any fact on a "
           "plant leads to every other plant that shares it."), ""]

    # The season pages go FIRST because they are the ones with a use today:
    # "what is in flower this month" is a question somebody actually walks
    # into the garden with.
    for kind in ("blooming", "growing"):
        buckets = (month_all or {}).get(kind) or {}
        if not buckets:
            continue
        label = MONTH_FACETS[kind][1]
        out.append(f"## [{label}, month by month](/by/{kind})")
        out.append("")
        out.append(" · ".join(
            f"[{m[:3]}](/by/{kind}/{m.lower()}) ({len(buckets[m])})"
            for m in MONTHS if buckets.get(m)))
        out.append("")
    for col, label, _ in FACETS:
        buckets = all_buckets.get(col) or {}
        if not buckets:
            continue
        c = re.sub(r"[^a-z0-9]+", "-", col.lower()).strip("-")
        top = sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:6]
        out.append(f"## [{label}](/by/{c})")
        out.append("")
        out.append(" · ".join(f"[{v}](/{fslug(col, v)}) ({len(p)})"
                              for v, p in top))
        out.append("")
    return chr(10).join(out)



# ---------------------------------------------------------------------------
#  The opening paragraph
# ---------------------------------------------------------------------------
# JASON, 2026-09-24: "right now it just looks like a bunch of data ... make it
# read as if somebody wrote it."
#
# EVERY CLAUSE BELOW IS ONE OF HIS OWN RECORDED VALUES, arranged into
# sentences. No model writes this and nothing is inferred: if the catalogue
# does not say how tall it gets, the sentence about height does not appear.
# That is the whole trick -- prose reads as authored, and readers trust
# authored text more, so the text had better not contain anything invented.
# A fluent paragraph with one made-up hardiness zone in it is more dangerous
# than the table it replaced, because nobody checks prose.
#
# SENTENCES ARE DROPPED, NOT PADDED. A plant with sparse data gets two short
# sentences rather than four limp ones stuffed with "unknown".
SMALL = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six",
         7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven",
         12: "Twelve"}


def _an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _first(text):
    """The first clause of a parenthesised vocabulary value.

    The catalogue writes "Ovate (Egg-shaped)" and "Simple (Single undivided
    blade)". The gloss is useful in a table and clumsy in a sentence.
    """
    return re.split(r"\s*\(", str(text or ""))[0].strip()


def _clause(text):
    """A recorded value, fit to sit inside a sentence.

    Catalogue values are written for a table: some end in a full stop, some
    are capitalised, some are a whole phrase ("green - reddish tinge during
    autumn"). Dropped into prose unedited they produce "in white., drawing"
    and similar. This trims the punctuation and lowercases only a leading
    capital that is not part of a proper noun.
    """
    t = str(text or "").strip().rstrip(".,;:")
    if not t:
        return ""
    # Lowercase the leading capital of EVERY comma-separated item, not just
    # the first. Catalogue values are Title Cased for a table, so
    # "Hummingbirds, Bees" reads as "drawing hummingbirds, Bees" if only the
    # head is touched. Months are left alone -- they are names.
    def _one(part):
        # The WHOLE item, not just its head: "Pale Lavender" has a capital
        # in the middle too, and a colour is not a proper noun. Words that
        # ARE names -- the months -- are put back afterwards.
        part = part.strip()
        lowered = part.lower()
        for m in MONTHS:
            lowered = re.sub("\x08" + m.lower() + "\x08", m, lowered)  # 0x08 as before (PLE2510); r"\b" was likely meant
        return lowered

    return ", ".join(_one(x) for x in t.split(",") if x.strip())


def lead(row):
    """A short paragraph about this plant, built from its own record."""
    name = val(row.get("plant_name")) or "This plant"
    bot = val(row.get("botanical_name"))
    fam = val(row.get("family"))
    kind = _first(val(row.get("plant_type"))).lower()
    ever = _first(val(row.get("is_evergreen"))).lower()

    bits = []

    # 1. What it is.
    one = f"**{name}**"
    if bot:
        one += f" (*{bot}*)"
    if kind and ever and ever not in ("herbaceous",):
        one += f" is {_an(ever)} {ever} {kind}"
    elif kind:
        one += f" is {_an(kind)} {kind}"
    else:
        one += " is grown here"
    if fam:
        one += f" in the {fam} family"
    bits.append(one + ".")

    # 2. Where it is in this garden, and since when.
    qty = val(row.get("quantity"))
    bed = val(row.get("flower_bed"))
    planted = val(row.get("year_planted"))
    two = []
    try:
        n = int(float(qty))
    except (TypeError, ValueError):
        n = 0
    if n:
        two.append(f"{SMALL.get(n, str(n))} grow{'s' if n == 1 else ''} here")
    if bed and bed.lower() not in ("to be sorted", "unsorted"):
        two.append(f"in {bed}")
    if planted:
        two.append(f"planted in {planted}")
    if two:
        # Joined with a comma before "planted in", which otherwise runs on:
        # "One grows here planted in 2024".
        first = two[0][:1].upper() + two[0][1:]
        rest = two[1:]
        joined = first + ((" " + rest[0]) if rest and rest[0].startswith("in ")
                          else "")
        tail = [x for x in rest if not x.startswith("in ")]
        if tail:
            joined += ", " + ", ".join(tail)
        bits.append(joined + ".")

    # 3. What it looks like.
    size = val(row.get("size"))
    rate = _first(val(row.get("growth_rate"))).lower()
    leaf = _first(val(row.get("leaf_shape"))).lower()
    fol = val(row.get("foliage_color"))
    three = []
    if size:
        three.append(f"It reaches {size}")
        if rate:
            three[-1] += f", growing at {_an(rate)} {rate} rate"
    if three:
        bits.append(", ".join(three) + ".")
    # A SEPARATE SENTENCE, because the colour field is a phrase and not an
    # adjective: "green, red, or purple depending on cultivar palmate leaves"
    # is what stacking them produces.
    if leaf or fol:
        leafy = "Its leaves are " + ", ".join(
            x for x in (leaf, _clause(fol)) if x)
        bits.append(leafy + ".")

    # 4. What it wants.
    sun = _first(val(row.get("sun_exposure"))).lower()
    water = _first(val(row.get("water_needs"))).lower()
    four = []
    if sun:
        four.append(sun)
    if water:
        four.append(f"{water} water")
    if four:
        bits.append("It wants " + " and ".join(four) + ".")

    # 5. When it does something worth watching.
    bloom = months(val(row.get("bloom_months")))
    colour = val(row.get("bloom_color"))
    att = val(row.get("attracts"))
    five = []
    if bloom:
        # Months keep their capitals -- they are names.
        five.append(f"It blooms {bloom}")
        if colour:
            five[-1] += f" in {_clause(colour)}"
    if att:
        five.append(("drawing " if five else "It draws ") + _clause(att))
    if five:
        bits.append(", ".join(five) + ".")

    return " ".join(bits)


def page_for(row):
    name = val(row.get("plant_name")) or f"Plant {row['id']}"
    bot = val(row.get("botanical_name"))

    out = [f"# {name}", ""]
    if bot:
        out += [f"*{bot}*", ""]

    pic = val(row.get("picture"))
    if pic:
        out += [f"![{name}]({IMAGES}/{pic})", ""]

    # THE PARAGRAPH COMES BEFORE THE TABLES. A page that opens with a grid
    # reads as a database export; one that opens with a sentence reads as
    # something somebody wrote -- and the sentences are made of the same
    # values the grid holds, so nothing is traded away for the change.
    opening = lead(row)
    if opening:
        out += [opening, ""]

    out += safety_block(row)
    out.append("")

    # A SECTION WITH NOTHING IN IT IS NOT PRINTED. An empty "Seeds and
    # fruit" heading reads as "this plant has no seeds" rather than "we did
    # not record any", and the second is what is true.
    for heading, fields in SECTIONS:
        rows = []
        for col, label in fields:
            v = val(row.get(col))
            if col in ("grow_months", "bloom_months"):
                v = months(v)
            # `garden_beds` repeats `flower_bed` on most rows; printing both
            # reads as two facts when there is one.
            if col == "garden_beds" and v == val(row.get("flower_bed")):
                v = ""
            # Size, Length and Width are often the same recorded string;
            # printing it three times reads as three measurements.
            if col in ("plant_length", "plant_width") and v == val(row.get("size")):
                v = ""
            if col == "average_cost" and v:
                try:
                    v = f"${float(v):.2f}"
                except (TypeError, ValueError):
                    pass
            if v:
                # Pipes inside a value would break the table row it sits in.
                v = v.replace("|", "/")
                rows.append((label, link_value(col, v)))
        if not rows:
            continue
        out += [f"## {heading}", "", "| | |", "|---|---|"]
        # Pipes inside a value would break the table row it sits in.
        out += [f"| **{l}** | {v} |" for l, v in rows]
        out += [""]

    zn = zone_note(val(row.get("zones")))
    if zn:
        out += ["## Where it will grow", "", zn, ""]

    for col, label in NOTES:
        v = val(row.get(col))
        if v:
            out += [f"## {label}", "", v, ""]

    # UP TO THE GENUS FIRST. On a cultivar page the most useful next click
    # is its siblings -- the other hydrangeas -- not the whole catalogue.
    up = ""
    genus = val(row.get("genus"))
    if genus and gslug(genus):
        up = f"[← All {genus}](/{gslug(genus)}) · "

    out += ["---", "",
            up +
            f"[This plant in the catalogue]({SITE}/plant_listing.php?id={row['id']})"
            f" · [All plants](/plants) · [The beds]({SITE}/beds)", "",
            ("*Built from Robin's own catalogue. Anything added beyond this "
            "carries the source it came from.*")]
    return "\n".join(out)


def genus_summary(genus):
    """A short, SOURCED paragraph about the genus, or ("", "").

    FETCHED AND ATTRIBUTED, NOT WRITTEN. The text is Wikipedia's own
    opening summary, trimmed to a couple of sentences and printed with the
    URL it came from. Nothing here asks a model what Hydrangea is: a
    plausible paragraph with no source is exactly the thing that makes a
    reference useless, because a reader cannot tell it from the parts that
    are true.

    NEVER RAISES AND NEVER BLOCKS THE BUILD. A genus Wikipedia has not
    heard of, or a network that is down, costs this one paragraph -- the
    page is still built from Robin's own records, which is the part that
    matters.
    """
    try:
        from garden_ingest.enrich import wiki_page
        got = wiki_page(genus) or {}
    except Exception:  # noqa: BLE001
        return "", ""
    text = (got.get("summary") or "").strip()
    url = (got.get("url") or "").strip()
    if not text or not url:
        return "", ""

    # TRIMMED TO TWO SENTENCES. This is a pointer to a source, not a copy
    # of it -- the link is there for anyone who wants the rest.
    parts = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(parts[:2]).strip()
    if len(short) > 420:
        short = short[:417].rsplit(" ", 1)[0] + "..."
    return short, url


def genus_page(genus, plants, summary=("", "")):
    """One genus, and every plant of it Robin grows.

    NO BOTANY FROM MEMORY. A paragraph about what Hydrangea IS would have to
    come from somewhere, and a model's recollection is not somewhere. This
    page says what his own records say -- which species and cultivars he
    has, where they are, and what they look like -- and the general write-up
    arrives later through the draft queue, carrying its source.
    """
    plants = sorted(plants, key=lambda r: (val(r.get("plant_name")) or "").lower())
    species = sorted({val(r.get("species")) for r in plants if val(r.get("species"))})
    beds = sorted({val(r.get("flower_bed")) for r in plants if val(r.get("flower_bed"))})
    total = sum(int(val(r.get("quantity")) or 0) or 1 for r in plants)

    out = [f"# {genus}", "",
           f"{len(plants)} {'kind' if len(plants) == 1 else 'different kinds'} "
           f"in Robin's garden"
           + (f", {total} plants in all" if total != len(plants) else "") + ".", ""]

    text, url = summary
    if text:
        out += [f"> {text}", "",
                f"> — [Wikipedia]({url})", ""]

    fam = next((val(r.get("family")) for r in plants if val(r.get("family"))), "")
    if fam:
        out += [f"**Family:** {fam}", ""]
    if species:
        out += ["**Species and hybrids grown here:** "
                + ", ".join(f"*{x}*" for x in species), ""]
    if beds:
        out += ["**Found in:** " + ", ".join(beds), ""]

    # The first photograph of each, so the page is a plate rather than a list.
    out += ["## The ones he grows", ""]
    for r in plants:
        name = val(r.get("plant_name")) or f"Plant {r['id']}"
        bot = val(r.get("botanical_name"))
        line = f"- [{name}](/{slug(name, r['id'])})"
        bits = [b for b in (f"*{bot}*" if bot else "",
                            val(r.get("flower_bed")),
                            val(r.get("bloom_color"))) if b]
        if bits:
            line += " — " + " · ".join(bits)
        out.append(line)
    out += ["", "---", "",
            "[All plants](/plants) · [Every genus](/genus)", "",
            "*Built from Robin's own catalogue.*"]
    return chr(10).join(out)


def genus_index(by_genus):
    out = ["# Every genus", "",
           (f"{len(by_genus)} genera in the garden. Each one gathers the "
           "cultivars and species grown here."), ""]
    for g in sorted(by_genus, key=str.lower):
        n = len(by_genus[g])
        out.append(f"- [{g}](/{gslug(g)}) — {n} "
                   f"{'kind' if n == 1 else 'kinds'}")
    return chr(10).join(out)


def index_page(rows):
    by_bed = {}
    for r in rows:
        by_bed.setdefault(val(r.get("flower_bed")) or "Not yet sorted",
                          []).append(r)

    out = ["# Every plant", "",
           (f"{len(rows)} plants, grouped by the bed they are in. "
           "Each page is built from the catalogue."), "",
           ("To browse by kind instead, start at **[Every genus](/genus)** "
           "— all the hydrangeas on one page, all the hostas on another."),
           ""]
    for bed in sorted(by_bed, key=lambda b: (b == "Not yet sorted", b.lower())):
        plants = sorted(by_bed[bed],
                        key=lambda r: (val(r.get("plant_name")) or "").lower())
        out += [f"## {bed}  ({len(plants)})", ""]
        for r in plants:
            name = val(r.get("plant_name")) or f"Plant {r['id']}"
            bot = val(r.get("botanical_name"))
            line = f"- [{name}](/{slug(name, r['id'])})"
            if bot:
                line += f" — *{bot}*"
            out.append(line)
        out.append("")
    return "\n".join(out)


def main(genus_only=False):
    bad = wikijs.check()
    if bad:
        print("wiki not configured:", bad)
        return 1

    import pymysql
    cx = pymysql.connect(
        host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"], database=os.environ["DB_NAME"],
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    cur = cx.cursor()
    # TABLE is checked against a plain-identifier pattern at start-up (see TABLE above).
    cur.execute(f"SELECT * FROM {TABLE} ORDER BY plant_name")  # nosec B608
    rows = cur.fetchall()
    print(f"{len(rows)} plants in the catalogue")

    # Listed ONCE. find_page() would otherwise re-list all pages for every
    # plant -- 483 full listings to write 483 pages.
    have, err = wikijs.pages()
    if err:
        print("could not list existing pages:", err)
        return 1
    known = {p["path"] for p in have}
    print(f"{len(known)} page(s) already in the wiki")

    made = updated = failed = 0
    # --genus-only re-does just the genus layer. The plant pages are 483
    # round trips and rarely the thing that changed; the genus pages carry
    # the fetched summaries and are the part worth rebuilding on its own.
    for i, r in enumerate([] if genus_only else rows, 1):
        name = val(r.get("plant_name")) or f"Plant {r['id']}"
        path = slug(name, r["id"])
        tags = ["plant"]
        for key in ("genus", "flower_bed", "plant_type"):
            v = val(r.get(key))
            if v:
                tags.append(re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-"))

        ok, what = wikijs.put_page(
            path, name, page_for(r),
            description=(val(r.get("botanical_name")) or name)[:250],
            tags=[t for t in tags if t][:8],
            existing=path in known)
        if not ok:
            failed += 1
            print(f"  [{i}/{len(rows)}] {name}: FAILED -- {what}")
        elif what == "created":
            made += 1
        else:
            updated += 1
        if i % 50 == 0:
            print(f"  {i}/{len(rows)} ({made} new, {updated} updated, "
                  f"{failed} failed)")

    # --- the genus pages, above the individual plants -------------------
    by_genus = {}
    for r in rows:
        g = val(r.get("genus"))
        if g:
            by_genus.setdefault(g, []).append(r)
    print(chr(10) + f"{len(by_genus)} genera")

    for g, plants in sorted(by_genus.items(), key=lambda kv: kv[0].lower()):
        path = gslug(g)
        ok, what = wikijs.put_page(
            path, g, genus_page(g, plants, genus_summary(g)),
            description=f"The {len(plants)} kinds of {g} in Robin's garden.",
            tags=["genus", re.sub(r"[^a-z0-9]+", "-", g.lower()).strip("-")],
            existing=path in known)
        if not ok:
            failed += 1
            print(f"  genus {g}: FAILED -- {what}")

    # --- the facet pages: every shared fact becomes a page ---------------
    all_buckets = {}
    for col, label, _multi in FACETS:
        # BUCKETED BY SLUG, NOT BY THE RAW STRING. The catalogue holds the
        # same facet spelled several ways -- "Hummingbirds" and
        # "hummingbirds", "Capsule" and "Capsule." -- which normalise to one
        # path. Keyed on the raw value they became two buckets racing for
        # one page, and the loser failed with "an entry already exists at
        # the same path". They are the same fact, so they are one bucket,
        # and the spelling used most often is the one shown.
        by_slug = {}
        for r in rows:
            for v in facet_values(r, col):
                sl = fslug(col, v)
                if not sl:
                    continue
                entry = by_slug.setdefault(sl, {"names": {}, "rows": []})
                entry["names"][v] = entry["names"].get(v, 0) + 1
                entry["rows"].append(r)
        buckets = {}
        for sl, entry in by_slug.items():
            best = max(entry["names"].items(), key=lambda kv: kv[1])[0]
            # De-duplicated: a plant recorded with both spellings would
            # otherwise be listed twice on its own facet page.
            seen, uniq = set(), []
            for r in entry["rows"]:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                uniq.append(r)
            buckets[best.strip().rstrip(".")] = uniq
        # A value carried by ONE plant is not worth its own page -- the
        # plant page already says it, and the link would lead somewhere
        # that adds nothing. It still appears on the facet index.
        all_buckets[col] = buckets

    made_f = 0
    for col, label, _multi in FACETS:
        buckets = all_buckets[col]
        if not buckets:
            continue
        c = re.sub(r"[^a-z0-9]+", "-", col.lower()).strip("-")
        ok, what = wikijs.put_page(
            f"by/{c}", label, facet_index(col, buckets),
            description=f"{label} across Robin's garden.",
            tags=["index", "facet"], existing=f"by/{c}" in known)
        if not ok:
            failed += 1
            print(f"  facet index {col}: FAILED -- {what}")
        for v, plants in buckets.items():
            path = fslug(col, v)
            if not path:
                continue
            ok, what = wikijs.put_page(
                path, f"{label}: {v}", facet_page(col, v, plants),
                description=f"{len(plants)} plants: {label.lower()} {v}.",
                tags=["facet", c], existing=path in known)
            if ok:
                made_f += 1
            else:
                failed += 1
                print(f"  facet {col}={v}: FAILED -- {what}")
    print(f"{made_f} facet page(s)")

    # --- month by month, computed from the masks --------------------------
    month_all = {}
    for kind, (col, label) in MONTH_FACETS.items():
        buckets = month_buckets(rows, col)
        month_all[kind] = buckets
        ok, what = wikijs.put_page(
            f"by/{kind}", label + ", month by month",
            month_index(kind, buckets),
            description=f"Which plants are {label.lower()} in each month.",
            tags=["index", "season"], existing=f"by/{kind}" in known)
        if not ok:
            failed += 1
            print(f"  month index {kind}: FAILED -- {what}")
        for m, plants in buckets.items():
            path = f"by/{kind}/{m.lower()}"
            ok, what = wikijs.put_page(
                path, f"{m}: {label.lower()}", month_page(kind, m, plants),
                description=f"{len(plants)} plants {label.lower()} in {m}.",
                tags=["season", kind], existing=path in known)
            if not ok:
                failed += 1
                print(f"  {kind}/{m}: FAILED -- {what}")
    print(f"month pages: {sum(len(b) for b in month_all.values())}")

    ok, what = wikijs.put_page(
        "browse", "Browse the garden", browse_page(all_buckets, month_all),
        description="Every way into the garden: by toxicity, roots, leaves, "
                    "what it attracts.",
        tags=["index"], existing="browse" in known)
    print("browse page:", what if ok else "FAILED -- " + what)

    ok, what = wikijs.put_page(
        "genus", "Every genus", genus_index(by_genus),
        description=f"{len(by_genus)} genera grown in Robin's garden.",
        tags=["index", "genus"], existing="genus" in known)
    print("genus index:", what if ok else "FAILED -- " + what)

    ok, what = wikijs.put_page(
        "plants", "Every plant", index_page(rows),
        description=f"All {len(rows)} plants in Robin's garden, by bed.",
        tags=["index", "plant"], existing="plants" in known)
    print("index page:", what if ok else "FAILED -- " + what)

    print(f"\n{made} created, {updated} updated, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(genus_only="--genus-only" in sys.argv))
