"""The console — one tab per thing you run, all in one page.

WHAT CHANGED AND WHY
--------------------
This used to be a dropdown: pick a file, edit it, pick another. That is the
right shape when you know which file you want and the wrong one when you are
running a machine. Seven sites and a dropdown means the answer to "what is on
this box" is a list you have to open one at a time.

So it is tabs now. Every project is visible at once, each tab holds the tools
for THAT project rather than a generic JSON box, and the tabs that have a
better tool elsewhere -- the recipe importer, the knowledge-base page -- link
straight to it instead of pretending a grid is the right way in.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
The Rust admin next door still owns uploading a file and rolling one back, and
still owns the password. This is the comfortable half: grids, search, forms.
If this app is broken or stopped, everything it edits is still editable there,
which is the whole reason the split exists. Nothing here is on the public path.

WHAT PROTECTS IT
----------------
Nothing, on its own -- and that is correct. Caddy asks the Rust admin whether
you are signed in before it proxies a byte here (forward_auth), so this app has
no password of its own to get wrong. If you can open this page you already got
past the argon2 password next door.
"""
from __future__ import annotations

import json
import os

import streamlit as st

import store
from targets import TARGETS

st.set_page_config(page_title="Console · admin", page_icon="🎛️", layout="wide")

# NOT st.secrets. Reading st.secrets when no secrets.toml exists does not
# return a default - it raises FileNotFoundError, at import time, so the whole
# page is a stack trace. There is no secrets file here and there should not be.
ADMIN_HOME = os.getenv("ADMIN_PATH", "/admin").rstrip("/")

# The tools that are better than a grid, for the things that have one. Each of
# these is a real page built for that job; sending somebody to a JSON editor
# when one of these exists is the console failing at its only purpose.
TOOLS = {
    "mom-kb": ("Import a page into the knowledge base", "/mama/kb/manage",
               "Paste a link. One of your models reads it and writes a guide "
               "in plain words, with pictures you pick. It lands as a draft "
               "only you can see."),
    "mom": ("Mama's guides", "/mama/kb",
            "The published guides, as she sees them."),
    "booking": ("The booking site", "/dee/",
                "What clients actually see and book through."),
    "legacy": ("The photo albums", "/legacy/",
               "What is published, as visitors see it."),
    "jason": ("Your page", "/jason/", ""),
    "dashboard": ("The landing page", "/", ""),
    "places": ("", "", ""),
}

st.markdown("""
<style>
  .muted{color:#8b96a5;font-size:.9rem;margin:.2rem 0 .8rem}
  .block-container{padding-top:2.2rem;max-width:1500px}
  h1{font-size:1.7rem !important}
  .stTabs [data-baseweb="tab"]{font-size:.95rem}
  .tool{background:#0d1826;border:1px solid #1e3350;border-radius:10px;
        padding:.9rem 1.1rem;margin:.2rem 0 1rem}
  .tool a{font-weight:600}
</style>
""", unsafe_allow_html=True)


def target_of(key: str) -> dict:
    t = dict(TARGETS[key])
    t["key"] = key
    return t


def stamp_for(key: str, disk_mtime: float) -> str:
    """Per-file, so switching tabs does not carry another file's timestamp
    along and produce a spurious "somebody changed this on disk"."""
    stamp_key = f"stamp::{key}"
    if st.session_state.get(stamp_key) is None:
        st.session_state[stamp_key] = disk_mtime
    return stamp_key


def history(t: dict, stamp_key: str):
    hist = store.backups(t)
    if not hist:
        return
    with st.expander(f"Previous versions ({len(hist)})"):
        st.caption(
            "Kept automatically before every save, by this page and by the "
            "upload pages alike. Restoring backs up what is there now first, "
            "so picking the wrong one is itself undoable."
        )
        for name, ts in hist:
            a, b = st.columns([4, 1])
            a.write(f"`{name}` · {store.describe_age(ts)}")
            if b.button("Restore", key=f"r::{name}"):
                try:
                    store.restore(t, name)
                except (OSError, ValueError) as e:
                    st.error(str(e))
                else:
                    st.session_state[stamp_key] = store.mtime(t)
                    st.success(f"Restored {name}.")
                    st.rerun()


def columns_for(t: dict, records: list) -> list:
    """The target's columns first, then anything else the data actually has.

    A field somebody added by hand must never be invisible here, because
    invisible means it silently disappears on the next save.
    """
    seen = list(t.get("columns") or [])
    for r in records:
        for k in r:
            if k not in seen and k != "id":
                seen.append(k)
    return seen


def grid(t: dict, records: list, stamp_key: str, key_suffix: str = ""):
    """The spreadsheet editor, and the save that refuses to write half a table."""
    seen = columns_for(t, records)
    table = []
    for r in records:
        row = {c: store.flatten(r.get(c)) for c in seen}
        row["id"] = r.get("id", "")
        table.append(row)

    st.caption(
        "Click a cell to edit. The bottom row adds one. Tick the box on the "
        "left and press delete to remove one. Fields holding several things — "
        "symptoms, steps, hours — take one per line."
    )
    edited = st.data_editor(
        table, num_rows="dynamic", use_container_width=True,
        key=f"grid::{t['key']}{key_suffix}",
        column_config={
            "id": st.column_config.TextColumn(
                "id", help="Left blank, this is worked out from the "
                           f"{t.get('name_key', 'name')}."),
            **{c: st.column_config.TextColumn(c, width="medium")
               for c in seen if c in store.LIST_FIELDS},
        },
    )

    c1, c2, _ = st.columns([1, 1, 5])
    if c2.button("Reload", key=f"reload::{t['key']}{key_suffix}"):
        st.session_state[stamp_key] = store.mtime(t)
        st.rerun()

    if c1.button("Save", type="primary", key=f"save::{t['key']}{key_suffix}"):
        cleaned, problems = store.clean_rows(t, edited, seen)
        if problems:
            # NOTHING is written. A partial save of a table is worse than no
            # save, because you cannot tell by looking which half went.
            for p in problems:
                st.error(p)
            st.warning("Nothing was saved.")
            return
        try:
            backup = store.save_rows(t, cleaned, st.session_state[stamp_key])
        except store.Stale as e:
            st.error(str(e))
        except OSError as e:
            st.error(f"Could not write the file: {e}")
        else:
            st.session_state[stamp_key] = store.mtime(t)
            msg = f"Saved. {len(cleaned)} rows are live now."
            if backup:
                msg += f" Previous version kept as {backup}."
            st.success(msg)


def document(t: dict, stamp_key: str):
    st.caption(
        "This file is a set of settings, not a table — there are no rows to "
        "add. The JSON is checked before anything is written, so a typo "
        "cannot reach the site."
    )
    doc = store.load(t)
    current = (json.dumps(doc, indent=2, ensure_ascii=False)
               if doc is not None else "{\n  \n}")
    text = st.text_area("Contents", value=current, height=420,
                        key=f"doc::{t['key']}")

    c1, c2, _ = st.columns([1, 1, 5])
    if c2.button("Reload", key=f"dreload::{t['key']}"):
        st.session_state[stamp_key] = store.mtime(t)
        st.session_state.pop(f"doc::{t['key']}", None)
        st.rerun()

    if c1.button("Save", type="primary", key=f"dsave::{t['key']}"):
        try:
            parsed = json.loads(text)
        except ValueError as e:
            st.error(f"That is not valid JSON, so nothing was changed.\n\n{e}")
            return
        try:
            backup = store.save(t, parsed, st.session_state[stamp_key])
        except store.Stale as e:
            st.error(str(e))
        except OSError as e:
            st.error(f"Could not write the file: {e}")
        else:
            st.session_state[stamp_key] = store.mtime(t)
            st.success("Saved." + (f" Previous version kept as {backup}."
                                   if backup else ""))


def places_tab(t: dict, stamp_key: str):
    """1,638 rows needs finding before it needs editing.

    A raw grid of that many rows is technically an editor and practically a
    wall. The search and the category filter are what make it a tool: you come
    here to change ONE place, and the job is finding it.

    EVERY FIELD IS SHOWN, including hours and the GPS numbers. They are in the
    file, so they are on the page -- a column this hides is a column that
    silently disappears the next time somebody saves.
    """
    records = store.rows(t)
    if not records:
        st.info("No places yet.")
        return

    cats = sorted({str(r.get("category", "")) for r in records if r.get("category")})
    cities = sorted({str(r.get("city", "")) for r in records if r.get("city")})

    c1, c2, c3 = st.columns([3, 2, 2])
    find = c1.text_input("Search", placeholder="name, address, phone…",
                         key="places_find")
    cat = c2.selectbox("Category", ["all"] + cats, key="places_cat")
    city = c3.selectbox("City", ["all"] + cities, key="places_city")

    shown = records
    if find.strip():
        needle = find.strip().lower()
        shown = [r for r in shown
                 if any(needle in str(v).lower() for v in r.values())]
    if cat != "all":
        shown = [r for r in shown if str(r.get("category", "")) == cat]
    if city != "all":
        shown = [r for r in shown if str(r.get("city", "")) == city]

    st.markdown(
        f'<p class="muted">{len(shown)} of {len(records)} places'
        + (' · <b>filtered — save is off</b>' if len(shown) != len(records) else '')
        + '</p>', unsafe_allow_html=True)

    if len(shown) != len(records):
        # SAVE IS OFF WHILE FILTERED, and this is the important bit.
        # store.save_rows writes the grid as the WHOLE file. Saving a filtered
        # view would therefore delete every place not currently on screen --
        # search for "taco", press save, lose 1,600 records. Rather than build
        # a merge that has to be right every time, the button is simply not
        # offered until you are looking at everything.
        st.warning(
            "Editing is off while a filter is on. Saving would write only "
            "what you can see and delete the rest. Clear the search and the "
            "two dropdowns to edit.")
        st.dataframe(shown, use_container_width=True, height=520)
        # The map is free and it is the fastest way to spot a wrong pin.
        pins = [{"lat": float(r["latitude"]), "lon": float(r["longitude"])}
                for r in shown
                if str(r.get("latitude", "")).replace(".", "").replace("-", "").isdigit()
                and str(r.get("longitude", "")).replace(".", "").replace("-", "").isdigit()]
        if pins:
            st.map(pins, size=40)
        return

    grid(t, records, stamp_key)


# --------------------------------------------------------------------- page --
st.markdown(f"[← the admin]({ADMIN_HOME}/)")
st.title("Console")
st.markdown(
    '<p class="muted">Everything this machine runs, one tab each. Nothing is '
    'saved until you press Save, and the version before every save is kept.</p>',
    unsafe_allow_html=True)

ORDER = ["places", "mom-kb", "mom", "booking", "legacy", "dashboard", "jason"]
keys = [k for k in ORDER if k in TARGETS] + [k for k in TARGETS if k not in ORDER]
tabs = st.tabs([TARGETS[k]["label"] for k in keys] + ["Files"])

for tab, key in zip(tabs, keys):
    with tab:
        t = target_of(key)
        disk_mtime = store.mtime(t)
        stamp_key = stamp_for(key, disk_mtime)

        left, right = st.columns([4, 1])
        left.markdown(f'<p class="muted">{t["hint"]}</p>', unsafe_allow_html=True)
        right.caption(
            f"last changed {store.describe_age(disk_mtime)}" if disk_mtime > 0
            else "not created yet — running on defaults")

        label, href, why = TOOLS.get(key, ("", "", ""))
        if label and href:
            st.markdown(
                f'<div class="tool"><a href="{href}" target="_blank">{label} →</a>'
                + (f'<div class="muted" style="margin:.3rem 0 0">{why}</div>'
                   if why else "")
                + '</div>', unsafe_allow_html=True)

        if key == "places":
            places_tab(t, stamp_key)
        elif t["shape"] == "list":
            grid(t, store.rows(t), stamp_key)
        else:
            document(t, stamp_key)

        history(t, stamp_key)

# ======================================================================= files
with tabs[-1]:
    st.markdown(
        '<p class="muted">Uploading a replacement file and rolling one back '
        'live in the Rust admin, on purpose: they are the operations you need '
        'when something here is broken, so they do not depend on this app '
        'being up.</p>', unsafe_allow_html=True)
    for key in keys:
        t = target_of(key)
        m = store.mtime(t)
        a, b = st.columns([4, 1])
        a.write(f"**{t['label']}** · `{t['file']}` · "
                + (store.describe_age(m) if m > 0 else "not created"))
        b.markdown(f"[Upload]({ADMIN_HOME}/site/{key})")

    st.divider()
    st.markdown(
        f"**People and passwords** live at [{ADMIN_HOME}/people]({ADMIN_HOME}/people). "
        "That one list is what lets somebody into every site on this machine — "
        "including the recipes and the knowledge base.")
