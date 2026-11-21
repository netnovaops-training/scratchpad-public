"""test_libraries.py - T0927 slice 2 proof: libraries as config + required settings. Run INSIDE the intake image:

    docker run --rm -v DROP.toml:/config/drop.toml:ro --entrypoint python IMAGE test_libraries.py

Checks: with no [library] section the tables are exactly the old code tables; overrides, switch-off and every
refusal; a broken section fails closed (LIBRARIES empty, people tables empty); classify() files book / comic /
recipe / unknown the same way as before; app.py refuses to start without each required setting.
Variables: fails (count of FAIL lines), OLD_GRANTS (the old classify.GRANT_FOR_KIND, copied from the live code).
"""
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import classify
import dropconfig as dc

OLD_GRANTS = {"recipe": "recipe", "workout": "gym", "document": "paperless", "book": "read", "comic": "read"}
fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


def refused(toml_text, why):
    try:
        dc.build_libraries(tomllib.loads(toml_text))
    except ValueError:
        check(f"refused: {why}", True)
        return
    check(f"refused: {why}", False)


# defaults = the old code
check("no [library]: LIBRARIES == defaults", dc.build_libraries({}) == dc.DEFAULT_LIBRARIES)
check("live config: GRANT_FOR_KIND == the old table", classify.GRANT_FOR_KIND == OLD_GRANTS)
check("live config loaded without error", dc.ERROR == "")

# overrides
lib = dc.build_libraries(tomllib.loads('[library.book]\nshelf = "manga"\ngrant = "reading"\n'
                                       '[library.recipe]\nenabled = false\n'))
check("override replaces one kind", lib["book"] == {"shelf": "manga", "grant": "reading"})
check("enabled = false removes the kind", "recipe" not in lib)
check("other kinds keep their defaults", lib["document"] == dc.DEFAULT_LIBRARIES["document"])

# refusals
refused('[library.malware]\ndest = "/srv/x"\ngrant = "a"\n', "a kind the classifier does not know")
refused('[library.book]\ndest = "/srv/x"\nshelf = "books"\ngrant = "read"\n', "both dest and shelf")
refused('[library.book]\ngrant = "read"\n', "neither dest nor shelf")
refused('[library.document]\ndest = "/etc/x"\ngrant = "paperless"\n', "dest outside /srv/")
refused('[library.document]\ndest = "/srv/../etc"\ngrant = "paperless"\n', "dest with ..")
refused('[library.document]\ndest = "/srv/{0}"\ngrant = "paperless"\n', "an unknown placeholder")
refused('[library.document]\ndest = "/srv/consume"\n', "no grant (a library nobody is granted)")
refused('[library.document]\ndest = "/srv/consume"\ngrant = "Bad Grant!"\n', "a grant that is not an app name")
refused('[library.book]\nshelf = "attic"\ngrant = "read"\n', "a shelf other than manga/books")
refused('[library.book]\nenabled = "no"\nshelf = "books"\ngrant = "read"\n', "enabled not true/false")
refused('[library.book]\nshelf = "books"\ngrant = "read"\ncolour = "red"\n', "an unknown key")

# fails closed
with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
    fh.write('[[person]]\nid = "ann"\nemails = ["ann@example.com"]\n[library.book]\nshelf = "attic"\ngrant = "read"\n')
tables, err = dc.load(Path(fh.name))
os.unlink(fh.name)
check("broken [library] fails closed: no libraries", tables["LIBRARIES"] == {})
check("... and no people (the whole file is refused)", tables["WHO"] == {} and "library.book" in err)

# classify() places files exactly as before
tmp = Path(tempfile.mkdtemp())
someone = next(iter(classify.SHELF), None)
if someone:
    (tmp / "a.epub").write_bytes(b"PK\x03\x04not really")
    v = classify.classify(str(tmp / "a.epub"), "a.epub", None, someone)
    check(f"book for {someone} -> their books shelf", v["kind"] == "book" and v["dest"] == classify.SHELF[someone]["books"])
    (tmp / "b.cbz").write_bytes(b"PK\x03\x04")
    v = classify.classify(str(tmp / "b.cbz"), "b.cbz", None, someone)
    check(f"comic for {someone} -> their manga shelf", v["kind"] == "comic" and v["dest"] == classify.SHELF[someone]["manga"])
    v = classify.classify(str(tmp / "a.epub"), "a.epub", None, None)
    check("book with no person -> unsorted", v["dest"] is None and "needs one to have a shelf" in v["why"])
    (tmp / "r.txt").write_text("2 eggs, flour. Mix and bake 20 minutes.")
    classify.ask_model = lambda filename, text: "recipe"   # no model in the sandbox
    v = classify.classify(str(tmp / "r.txt"), "r.txt", None, someone)
    check(f"recipe for {someone} -> pending/{someone}/recipe",
          v["dest"] == f"{classify.PENDING_ROOT}/{someone}/recipe" and "held for" in v["why"])
    v = classify.classify(str(tmp / "r.txt"), "r.txt", None, None)
    check("recipe with no person -> unsorted", v["dest"] is None)
    classify.ask_model = lambda filename, text: "document"
    v = classify.classify(str(tmp / "r.txt"), "r.txt", None, None)
    check("document -> /srv/consume (Paperless)", v["dest"] == "/srv/consume" and "Paperless decides" in v["why"])
    (tmp / "x.exe").write_bytes(b"MZ")
    v = classify.classify(str(tmp / "x.exe"), "x.exe", None, someone)
    check("unknown type -> unsorted", v["kind"] == "unknown" and v["dest"] is None)
    saved = classify.LIBRARIES
    classify.LIBRARIES = {k: v for k, v in saved.items() if k != "book"}
    v = classify.classify(str(tmp / "a.epub"), "a.epub", None, someone)
    check("book library switched off -> unsorted", v["dest"] is None)
    classify.LIBRARIES = saved
else:
    check("the config has at least one person with shelves (needed for the placement checks)", False)

# required settings
base = {k: v for k, v in os.environ.items() if k not in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL")}
full = dict(base, LLM_BASE="http://10.255.255.1", AUTH_LEVELS_URL="http://10.255.255.1/a",
            GOTENBERG_URL="http://10.255.255.1:3000", INTAKE_KEY="k-test")  # INTAKE_KEY: required since T0931
for name in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    env = {k: v for k, v in full.items() if k != name}
    r = subprocess.run([sys.executable, "-c", "import app"], env=env, capture_output=True, text=True, timeout=120)
    check(f"app refuses to start without {name}", r.returncode != 0 and name in r.stderr)
r = subprocess.run([sys.executable, "-c", "import app"], env=full, capture_output=True, text=True, timeout=120)
check("app starts with all three set", r.returncode == 0)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
