"""test_t0927s3.py - proof that both doors share ONE virus gate and ONE placement step (T0927 slice 3), and that
the filed results and sidecars are what they were. Run INSIDE the patched intake image (scanner and model faked):

    docker run --rm --entrypoint python IMAGE test_t0927s3.py

Variables: ROOT (throw-away folder), fails (FAIL count), gates / places (calls seen through the shared helpers).
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
    "CLAMD_HOST": "fake-clamd", "LLM_BASE": "http://10.255.255.1", "INTAKE_LLM_TIMEOUT": "1",
    "AUTH_LEVELS_URL": "http://10.255.255.1/a", "GOTENBERG_URL": "http://10.255.255.1:3000", "INTAKE_RESERVE_MB": "1",
})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import scan  # noqa: E402
from fastapi import HTTPException  # noqa: E402

fails = 0
gates, places = [], []


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


real_gate, real_place = app._virus_gate, app._place_or_unsorted
app._virus_gate = lambda *a, **k: (gates.append(a[3]), real_gate(*a, **k))[1]
app._place_or_unsorted = lambda *a, **k: (places.append(a[4]), real_place(*a, **k))[1]


def staged(text, name="x.xyz"):
    f = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp")) / name
    f.write_text(text)
    return f


def call(door, answer, text="hello"):
    scan.scan = lambda p: answer
    f = staged(text)
    try:
        if door == "drop":
            return 200, app.route_file(f, "x.xyz", 5, "someone@example.com")
        return 200, app._intake_sync(f, "x.xyz", 5, "jason", "12345", "telegram", "a caption")
    except HTTPException as e:
        return e.status_code, None


for door in ("drop", "intake"):
    g0, p0 = len(gates), len(places)
    code, _ = call(door, ("infected", "Eicar"))
    check(f"{door}: infected -> 422 through the shared gate, not placed", code == 422 and len(gates) == g0 + 1
          and len(places) == p0)
    code, _ = call(door, ("error", "down"))
    check(f"{door}: scanner down -> 503 through the shared gate", code == 503)
    code, r = call(door, ("clean", ""), text=f"clean {door} file")
    check(f"{door}: clean -> filed through the shared placement", code == 200 and len(places) == p0 + 1
          and pathlib.Path(r["path"]).read_text() == f"clean {door} file")
    side = json.loads(pathlib.Path(r["path"] + ".intake.json").read_text())
    if door == "drop":
        check("drop sidecar: uploader + source drop + sha256 + time, as before",
              side.get("uploader") == "someone@example.com" and side.get("source") == "drop" and "sha256" in side and "at" in side)
        check("drop result keeps its old keys (no sha256 added)", "sha256" not in r and r["size"] == 5)
    else:
        check("intake sidecar: sender + source + caption + sha256 + time, as before",
              side.get("sender") == "12345" and side.get("source") == "telegram" and side.get("caption") == "a caption"
              and "sha256" in side and "at" in side)
        check("intake result keeps sha256", "sha256" in r and r["size"] == 5)
check("held notes name door + person, never an email", all("@" not in g for g in gates) and
      any(g.startswith("drop:") for g in gates) and any(g.startswith("telegram:") for g in gates))
log = (ROOT / "unsorted" / "log").read_text() if (ROOT / "unsorted" / "log").exists() else ""
check("both doors now write a HELD log line", "HELD drop from" in log and "HELD telegram from" in log)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
