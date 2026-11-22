"""test_t0962.py - proof for T0962 points 1 and 3. Run INSIDE the intake image (no clamd needed: the scanner's
answer is faked, so every branch is tested, including "scanner down").

    docker run --rm --entrypoint python IMAGE test_t0962.py

Checks: /intake's worker (_intake_sync) holds an infected file (422) and a file the scanner could not check (503),
moves it to quarantine with the person+door (not the raw sender) in WHY.txt, and files a clean one; scanning off
= old behaviour; the same file held twice in the same second gets two folders, both WHY.txt kept.
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
    "INTAKE_KEY": "k-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"), "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
    "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "PENDING_ROOT": str(ROOT / "pending"),
    "INTAKE_QUARANTINE": str(ROOT / "quarantine"), "CLAMD_HOST": "fake-clamd", "LLM_BASE": "http://10.255.255.1",
    "INTAKE_LLM_TIMEOUT": "1", "AUTH_LEVELS_URL": "http://10.255.255.1/a", "GOTENBERG_URL": "http://10.255.255.1:3000",
})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import scan  # noqa: E402
from fastapi import HTTPException  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


def staged(text="hello"):
    d = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp"))
    f = d / "note.xyz"
    f.write_text(text)
    return f


calls = []   # every file the fake scanner was asked about


def run(answer):
    scan.scan = lambda path: calls.append(path) or answer
    f = staged()
    try:
        r = app._intake_sync(f, "note.xyz", 5, "jason", "12345", "telegram", None)
        return 200, r, f
    except HTTPException as e:
        return e.status_code, None, f


check("scanning is on in this test", scan.ENABLED)
before = set((ROOT / "quarantine").iterdir())
code, _, f = run(("infected", "Eicar-Test-Signature FOUND"))
held = set((ROOT / "quarantine").iterdir()) - before
check("infected via /intake -> 422", code == 422)
check("... the file left the staging folder", not f.exists())
check("... and sits in quarantine as .held", len(held) == 1 and any(p.suffix == ".held" for p in next(iter(held)).iterdir()))
why = json.loads((next(iter(held)) / "WHY.txt").read_text())
check("... WHY.txt names the person and door, not the raw sender id",
      why["email"] == "telegram:jason" and "12345" not in json.dumps(why))

code, _, f = run(("error", "connection refused"))
check("scanner down via /intake -> 503, held", code == 503 and not f.exists())

code, r, f = run(("clean", ""))
check("clean via /intake -> filed (200, unsorted here)", code == 200 and r and pathlib.Path(r["path"]).exists())

scan.ENABLED = False
asked_before = len(calls)
code, r, f = run(("infected", "ignored"))
# 11-21 doc review: this used to set a "must not be called" scanner that run() then replaced, so it proved
# nothing about the scanner; now the fake records every call and the check counts them.
check("scanning off -> old behaviour (filed, scanner never asked)", code == 200 and len(calls) == asked_before)
scan.ENABLED = True

# point 3: two holds of the same file in the same second
q0 = set((ROOT / "quarantine").iterdir())
a, b = staged("same"), staged("same")
fa = scan.hold(a, "same.txt", "x", "infected", "one", "d" * 64)
fb = scan.hold(b, "same.txt", "x", "infected", "two", "d" * 64)
check("same file, same second -> two folders", fa != fb and len(set((ROOT / "quarantine").iterdir()) - q0) == 2)
check("... both WHY.txt kept", json.loads((fa / "WHY.txt").read_text())["detail"] == "one"
      and json.loads((fb / "WHY.txt").read_text())["detail"] == "two")

# point 2: /healthz and the scanner
scan.ping = lambda timeout=3.0: True
r = app.healthz()
body = json.loads(r.body)
check("healthz: scanner up -> 200, clamav ok, held counted", r.status_code == 200 and body["clamav"] == "ok"
      and body["held"] == sum(1 for d in (ROOT / "quarantine").iterdir() if d.is_dir()))
scan.ping = lambda timeout=3.0: False
r = app.healthz()
body = json.loads(r.body)
check("healthz: scanner down -> 503 with a plain reason", r.status_code == 503 and body["clamav"] == "down"
      and any("virus check" in p for p in body["problems"]))
scan.ENABLED = False
r = app.healthz()
check("healthz: scanning off -> clamav off, not a problem", r.status_code == 200 and json.loads(r.body)["clamav"] == "off")
scan.ENABLED = True

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
