"""test_dropconfig.py - T0927 slice 1 proof: the config file gives EXACTLY the old tables, and bad files fail closed.

    DROP_CONFIG=<generated drop.toml> python3 test_dropconfig.py <intake source dir>
Prints PASS/FAIL lines and counts only (never an email). Exit 1 on any FAIL.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dropconfig as dc  # noqa: E402
import make_drop_config as mk  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


old = mk.tables(Path(sys.argv[1]).expanduser())
check("config loaded without error", dc.ERROR == "")
check(f"WHO equal ({len(old['WHO'])} emails)", dc.WHO == {k.lower(): v for k, v in old["WHO"].items()})
check(f"PRIVATE_OK equal ({len(old['PRIVATE_OK'])})", dc.PRIVATE_OK == {e.lower() for e in old["PRIVATE_OK"]})
check(f"NAMES equal ({len(old['NAMES'])})", dc.NAMES == old["NAMES"])
check(f"PEOPLE equal ({len(old['PEOPLE'])} aliases)", dc.PEOPLE == old["PEOPLE"])
check(f"SHELF equal ({len(old['SHELF'])})", dc.SHELF == old["SHELF"])
check("BRAND default Atomic", dc.BRAND == {"name": "Atomic Share", "short_name": "Atomic Share", "theme": "atomic"})

BAD = {
    "missing file": None,
    "not TOML": "this is [not toml",
    "bad email": '[[person]]\nid = "a"\nemails = ["not-an-address"]\n',
    "bad id": '[[person]]\nid = "../etc"\nemails = []\n',
    "same email twice": '[[person]]\nid = "a"\nemails = ["x@y.zz"]\n[[person]]\nid = "b"\nemails = ["x@y.zz"]\n',
    "shelf outside /srv": '[[person]]\nid = "a"\n[person.shelves]\nbooks = "/etc"\n',
    "shelf with ..": '[[person]]\nid = "a"\n[person.shelves]\nbooks = "/srv/../etc"\n',
    "private not theirs": '[[person]]\nid = "a"\nemails = ["x@y.zz"]\nprivate = ["q@y.zz"]\n',
    "unknown theme": '[brand]\ntheme = "space"\n',
}
with tempfile.TemporaryDirectory() as d:
    for name, text in BAD.items():
        p = Path(d) / "drop.toml"
        if text is None:
            p = Path(d) / "absent.toml"
        else:
            p.write_text(text, encoding="utf-8")
        t, err = dc.load(p)
        check(f"fails closed: {name}", bool(err) and not t["WHO"] and not t["PRIVATE_OK"] and not t["PEOPLE"])
    p = Path(d) / "garden.toml"
    p.write_text('[brand]\nname = "Garden Drop"\ntheme = "garden"\n[[person]]\nid = "robin"\nemails = ["j@x.zz"]\n',
                 encoding="utf-8")
    t, err = dc.load(p)
    check("garden brand + one person loads", not err and t["BRAND"]["theme"] == "garden" and t["WHO"] == {"j@x.zz": "robin"})
print(f"{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
