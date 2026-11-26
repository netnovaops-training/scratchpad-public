#!/usr/bin/env python3
"""patch_libraries.py - T0927 slice 2, in a COPY of intake only: libraries as config + required settings.

    python3 patch_libraries.py ~/sandbox/t0927s2/intake

Audit phase 4 items 3 and 4 (todo-code/ATOMIC-AUDIT-2026-11-19.md):
  3. Libraries as config: which kind of file goes where, and which admin-panel grant may add it, move from two
     code tables (classify.GRANT_FOR_KIND + the "where" block in classify.classify) into dropconfig.LIBRARIES.
     drop.toml may override a kind with [library.<kind>] (dest OR shelf, plus grant), or switch it off
     (enabled = false -> that kind always goes to unsorted). With no [library] section the tables are exactly the
     old ones, so the live drop.toml needs no change. A broken section fails CLOSED like the rest of the file:
     LIBRARIES = {} -> every file goes to unsorted, nothing is writable by grant.
  4. Required settings: LLM_BASE, AUTH_LEVELS_URL and GOTENBERG_URL lose their built-in personal addresses (the
     household's own domains were the defaults); app.py refuses to start if one is missing, naming it.
     compose.yml already sets LLM_BASE and AUTH_LEVELS_URL; the swap adds GOTENBERG_URL.
Also: test_t0931b.py's own environment gains the two new settings (it imports app.py directly).

Refuses on the live folder, if already patched, or if any piece of old code is not found exactly once.
Variables: folder (the copy), dc/cl/ap/sh/tb (file texts being patched), OLD_WHERE/NEW_WHERE (the replaced block).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")


def swap(text: str, old: str, new: str, name: str) -> str:
    """Replace `old` with `new`, refusing unless `old` is there exactly once."""
    if text.count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


# ---- dropconfig.py: LIBRARIES ----
dc = (folder / "dropconfig.py").read_text(encoding="utf-8")
if "LIBRARIES" in dc:
    raise SystemExit("already patched")
dc = swap(dc, '''    SHELF        person id -> {"manga": path, "books": path}  (was classify.SHELF)
''', '''    SHELF        person id -> {"manga": path, "books": path}  (was classify.SHELF)
    LIBRARIES    kind -> {"dest": path template | "shelf": manga|books, "grant": app}  (T0927 slice 2; was
                 classify.GRANT_FOR_KIND + the "where" block of classify.classify)

[library.<kind>] (optional, T0927 slice 2). Kinds: document, recipe, workout, book, comic (the classifier's closed
vocabulary; a config cannot invent a kind). A section REPLACES that kind's default; give exactly one of
dest (a path under /srv/; placeholders {person} and {pending} = PENDING_ROOT only) or shelf (manga | books, the
person's own shelf), plus grant (the app name in the admin panel that lets a person ADD this kind).
enabled = false switches a kind off: those files go to unsorted. Defaults (= the old code tables):
    document -> /srv/consume (paperless)          recipe  -> {pending}/{person}/recipe (recipe)
    workout  -> {pending}/{person}/workout (gym)  book    -> shelf books (read)     comic -> shelf manga (read)
''', "dropconfig.py")
dc = swap(dc, '''ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
''', '''ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
GRANT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
LIBRARY_KINDS = ("document", "recipe", "workout", "book", "comic")   # classify.KINDS minus "unknown"
DEFAULT_LIBRARIES = {
    "document": {"dest": "/srv/consume", "grant": "paperless"},
    "recipe": {"dest": "{pending}/{person}/recipe", "grant": "recipe"},
    "workout": {"dest": "{pending}/{person}/workout", "grant": "gym"},
    "book": {"shelf": "books", "grant": "read"},
    "comic": {"shelf": "manga", "grant": "read"},
}
''', "dropconfig.py")
dc = swap(dc, '''def load(path: Path = CONFIG_PATH)''', '''def build_libraries(data: dict) -> dict:
    """kind -> library from the defaults plus any [library.<kind>] sections. Raises ValueError on a bad one.

    Every library has a grant: a kind nobody is granted is not writable, and a config cannot make one that
    needs no grant at all (the old rule: an ungoverned kind goes to unsorted)."""
    lib = {k: dict(v) for k, v in DEFAULT_LIBRARIES.items()}
    sect = data.get("library", {})
    if not isinstance(sect, dict):
        raise ValueError("[library] must be a table")
    for kind, v in sect.items():
        if kind not in LIBRARY_KINDS:
            raise ValueError(f"library.{kind}: not a kind the drop knows ({', '.join(LIBRARY_KINDS)})")
        if not isinstance(v, dict) or not set(v) <= {"dest", "shelf", "grant", "enabled"}:
            raise ValueError(f"library.{kind}: the keys are dest, shelf, grant, enabled")
        if v.get("enabled", True) is False:
            del lib[kind]
            continue
        if v.get("enabled", True) is not True:
            raise ValueError(f"library.{kind}: enabled must be true or false")
        if ("dest" in v) == ("shelf" in v):
            raise ValueError(f"library.{kind}: give exactly one of dest or shelf")
        if "dest" in v:
            d = v["dest"]
            probe = d.replace("{pending}", "/srv/p").replace("{person}", "x") if isinstance(d, str) else ""
            if not probe.startswith("/srv/") or ".." in probe or "{" in probe or "}" in probe:
                raise ValueError(f"library.{kind}: dest must be a path under /srv/ "
                                 "(placeholders {person} and {pending} only)")
        elif v["shelf"] not in ("manga", "books"):
            raise ValueError(f"library.{kind}: shelf must be manga or books")
        if not (isinstance(v.get("grant"), str) and GRANT_RE.match(v["grant"])):
            raise ValueError(f"library.{kind}: grant must be an app name (a-z) from the admin panel")
        lib[kind] = {k: v[k] for k in ("dest", "shelf", "grant") if k in v}
    return lib


def load(path: Path = CONFIG_PATH)''', "dropconfig.py")
dc = swap(dc, '''    return {"BRAND": brand, "WHO": who, "PRIVATE_OK": private, "NAMES": names, "PEOPLE": people, "SHELF": shelf}
''', '''    return {"BRAND": brand, "WHO": who, "PRIVATE_OK": private, "NAMES": names, "PEOPLE": people, "SHELF": shelf,
            "LIBRARIES": build_libraries(data)}
''', "dropconfig.py")
dc = swap(dc, '''"PEOPLE": {}, "SHELF": {}}
''', '''"PEOPLE": {}, "SHELF": {},
             "LIBRARIES": {}}   # fails closed: no library is writable, everything goes to unsorted
''', "dropconfig.py")
dc = swap(dc, '''NAMES, PEOPLE, SHELF = _t["NAMES"], _t["PEOPLE"], _t["SHELF"]
''', '''NAMES, PEOPLE, SHELF = _t["NAMES"], _t["PEOPLE"], _t["SHELF"]
LIBRARIES = _t["LIBRARIES"]
''', "dropconfig.py")
compile(dc, "dropconfig.py", "exec")

# ---- classify.py: tables from LIBRARIES, no built-in LLM address ----
cl = (folder / "classify.py").read_text(encoding="utf-8")
cl = swap(cl, 'LLM_BASE = os.environ.get("LLM_BASE", "https://llm.home.example.net")\n',
          'LLM_BASE = os.environ.get("LLM_BASE", "")  # required; app.py refuses to start without it (T0927 slice 2)\n',
          "classify.py")
cl = swap(cl, "from dropconfig import PEOPLE, SHELF  # noqa: E402\n",
          "from dropconfig import LIBRARIES, PEOPLE, SHELF  # noqa: E402\n", "classify.py")
start = cl.index("GRANT_FOR_KIND = {")
end = cl.index("}\n", start) + 2
cl = cl[:start] + ('# T0927 slice 2: built from drop.toml [library.<kind>] (dropconfig.LIBRARIES); with no [library]\n'
                   '# section it is exactly the old table: recipe->recipe, workout->gym, document->paperless,\n'
                   '# book->read, comic->read. A kind with no entry still goes to unsorted.\n'
                   'GRANT_FOR_KIND = {kind: lib["grant"] for kind, lib in LIBRARIES.items()}\n') + cl[end:]
OLD_WHERE = cl[cl.index("    # --- where ---"):cl.index("    if dest is None:\n")]
NEW_WHERE = '''    # --- where -----------------------------------------------------------
    # T0927 slice 2: the library table (dropconfig.LIBRARIES, from drop.toml) says where each kind goes.
    # A shelf library uses the person's own shelf; a dest library may name {person} (then it needs one).
    dest = None
    lib = LIBRARIES.get(kind)
    if lib and "shelf" in lib:
        if person and lib["shelf"] in SHELF.get(person, {}):
            dest = SHELF[person][lib["shelf"]]
    elif lib and "dest" in lib and (person or "{person}" not in lib["dest"]):
        dest = lib["dest"].format(pending=PENDING_ROOT, person=person or "")
        if kind == "document":
            # Into Paperless, which then applies the tag -> owner -> app
            # routing that already exists. This module does not duplicate it.
            why.append("Paperless decides its tag and owner from there")
        elif "{person}" in lib["dest"]:
            # NOT written into the recipe app or the workout feed: they own their records and export a feed
            # to the index, so the file waits per person for an import call (see TODO.md).
            why.append(f"{kind} needs an import into its own app; held for {person}")
        else:
            why.append(f"filed to the {kind} library")
'''
if "dest = None" not in OLD_WHERE or '"/srv/consume"' not in OLD_WHERE:
    raise SystemExit("classify.py: the where-block is not the expected one")
cl = cl.replace(OLD_WHERE, NEW_WHERE)
compile(cl, "classify.py", "exec")

# ---- app.py: no built-in auth address; refuse to start without the required settings ----
ap = (folder / "app.py").read_text(encoding="utf-8")
ap = swap(ap, '''AUTH_LEVELS_URL = os.environ.get(
    "AUTH_LEVELS_URL", "https://example.org/auth/levels")
''', '''AUTH_LEVELS_URL = os.environ.get("AUTH_LEVELS_URL", "")  # required (T0927 slice 2), checked below
''', "app.py")
ap = swap(ap, '''import scan  # T0920 virus check
''', '''import scan  # T0920 virus check

# T0927 slice 2 (audit phase 4 item 4): these had the household's own addresses as built-in defaults, so a copy
# run by someone else would quietly ask OUR servers. Now they must be set (compose.yml), and the app refuses to
# start, naming what is missing, instead of guessing.
REQUIRED_SETTINGS = ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL")
_missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name, "").strip()]
if _missing:
    raise SystemExit(f"intake: required setting(s) missing: {', '.join(_missing)} (set them in compose.yml)")
''', "app.py")
compile(ap, "app.py", "exec")

# ---- share.py: Gotenberg address from the environment ----
sh = (folder / "share.py").read_text(encoding="utf-8")
sh = swap(sh, 'GOTENBERG = "http://gotenberg-web:3000"  # its own renderer, not Paperless\'s (see gotenberg_web_patch.py)\n',
          '# its own renderer, not Paperless\'s (see gotenberg_web_patch.py); required (T0927 slice 2), app.py checks it\n'
          'GOTENBERG = os.environ.get("GOTENBERG_URL", "").rstrip("/")\n', "share.py")
compile(sh, "share.py", "exec")

# ---- test_t0931b.py: it imports app.py itself, so it must set the new required settings ----
tb = (folder / "test_t0931b.py").read_text(encoding="utf-8")
tb = swap(tb, '''"LLM_BASE": "http://10.255.255.1", "INTAKE_LLM_TIMEOUT": "2",
''', '''"LLM_BASE": "http://10.255.255.1", "INTAKE_LLM_TIMEOUT": "2",
    "AUTH_LEVELS_URL": "http://10.255.255.1/auth/levels", "GOTENBERG_URL": "http://10.255.255.1:3000",
''', "test_t0931b.py")

for name, text in (("dropconfig.py", dc), ("classify.py", cl), ("app.py", ap), ("share.py", sh),
                   ("test_t0931b.py", tb)):
    (folder / name).write_text(text, encoding="utf-8")
print("patched: dropconfig.py classify.py app.py share.py test_t0931b.py")
