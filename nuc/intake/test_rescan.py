"""test_rescan.py - proof for rescan_held.py (T0962 point 4). Run INSIDE the intake image; the scanner is faked.

    docker run --rm --entrypoint python IMAGE test_rescan.py

Checks: an "error" hold is released to unsorted when the re-scan is clean (WHY.txt -> released, sidecar
written, file content intact); becomes "infected" when the re-scan finds something (file stays held);
is left alone while the scanner is still down; "infected" holds are never touched.
Variables: ROOT (throw-away folder), fails (FAIL count).
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp", "quarantine"):
    (ROOT / d).mkdir()
os.environ.update({
    "INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"), "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
    "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "INTAKE_QUARANTINE": str(ROOT / "quarantine"),
    "CLAMD_HOST": "fake-clamd", "LLM_BASE": "http://10.255.255.1", "AUTH_LEVELS_URL": "http://10.255.255.1/a",
    "GOTENBERG_URL": "http://10.255.255.1:3000",
})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rescan_held  # noqa: E402
import scan  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


def hold(text, state):
    f = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp")) / "doc.txt"
    f.write_text(text)
    return scan.hold(f, "doc.txt", "telegram:jason", state, "test", "e" * 64)


a = hold("first file", "error")
scan.scan = lambda p: ("clean", "")
c = rescan_held.main()
why = json.loads((a / "WHY.txt").read_text())
out = list((ROOT / "unsorted").glob("doc*.txt"))
check("scanner back + clean -> released", c["clean"] == 1 and why["state"] == "released")
check("... file now in unsorted with its content", len(out) == 1 and out[0].read_text() == "first file")
check("... sidecar says why", out[0].with_name(out[0].name + ".intake.json").exists()
      and "re-scanned" in json.loads(out[0].with_name(out[0].name + ".intake.json").read_text())["why"])
check("... nothing left held in that folder", not any(p.suffix == ".held" for p in a.iterdir()))

b = hold("second file", "error")
scan.scan = lambda p: ("infected", "Eicar FOUND")
rescan_held.main()
check("re-scan finds a virus -> stays held, state infected",
      json.loads((b / "WHY.txt").read_text())["state"] == "infected" and any(p.suffix == ".held" for p in b.iterdir()))

d = hold("third file", "error")
scan.scan = lambda p: ("error", "connection refused")
c = rescan_held.main()
check("scanner still down -> left alone", c["down"] == 1 and json.loads((d / "WHY.txt").read_text())["state"] == "error")

e = hold("fourth file", "infected")
calls = []
scan.scan = lambda p: calls.append(p) or ("clean", "")
rescan_held.main()
check("an infected hold is never re-scanned or released",
      json.loads((e / "WHY.txt").read_text())["state"] == "infected" and not any(str(e) in str(p) for p in calls))

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
