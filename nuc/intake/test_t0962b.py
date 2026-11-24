"""test_t0962b.py - proof for T0962 point 5 + the rescan follow-ups. Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_t0962b.py

Checks: a file held at the drop-page door (route_file) gets a WHY.txt with "drop:<person>" and no email address;
rescan_held runs one at a time (a held lock -> nothing done); a folder that fails is counted, named, and makes
the run exit 1 while the others still run; scan.held_count skips an unreadable folder instead of reporting 0.
The scanner is faked. Variables: ROOT (throw-away folder), fails (FAIL count).
"""
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp", "quarantine"):
    (ROOT / d).mkdir()
ENV = {
    "INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"), "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
    "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "INTAKE_QUARANTINE": str(ROOT / "quarantine"),
    "CLAMD_HOST": "fake-clamd", "LLM_BASE": "http://10.255.255.1", "AUTH_LEVELS_URL": "http://10.255.255.1/a",
    "GOTENBERG_URL": "http://10.255.255.1:3000", "INTAKE_RESERVE_MB": "1",
}
os.environ.update(ENV)
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import app  # noqa: E402
import rescan_held  # noqa: E402
import scan  # noqa: E402
from fastapi import HTTPException  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


def staged(text="hello", name="note.txt"):
    f = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp")) / name
    f.write_text(text)
    return f


# point 5: drop-page door
scan.scan = lambda p: ("infected", "Eicar FOUND")
before = set((ROOT / "quarantine").iterdir())
try:
    app.route_file(staged(), "note.txt", 5, "someone@example.com")
    code = 200
except HTTPException as e:
    code = e.status_code
new = set((ROOT / "quarantine").iterdir()) - before
why = json.loads((next(iter(new)) / "WHY.txt").read_text()) if new else {}
check("drop page: infected -> 422 and held", code == 422 and len(new) == 1)
check("... WHY.txt says drop:<person>, no email address", why.get("email", "").startswith("drop:")
      and "@" not in json.dumps(why))

# lock: a run while another holds the lock does nothing
held = scan.hold(staged("x"), "x.txt", "drop:unknown", "error", "down", "a" * 64)
scan.scan = lambda p: ("clean", "")
with open(scan.QUARANTINE / ".rescan.lock", "a") as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    c = rescan_held.main()
check("lock held by another run -> nothing done", c["clean"] == 0
      and json.loads((held / "WHY.txt").read_text())["state"] == "error")
c = rescan_held.main()
check("... after the lock is free the same folder is released", c["clean"] == 1)

# a failing folder: counted, named, exit 1, the others still run
bad = scan.hold(staged("y"), "y.txt", "drop:unknown", "error", "down", "b" * 64)
good = scan.hold(staged("z"), "z.txt", "drop:unknown", "error", "down", "c" * 64)
(bad / "WHY.txt").chmod(0o000)   # unreadable note -> json read fails -> "skipped", so break the held file instead
(bad / "WHY.txt").chmod(0o644)
for p in bad.iterdir():
    if p.suffix == ".held":
        p.chmod(0o000)               # scan.scan is faked, but app.sha256 must read the file -> PermissionError
r = subprocess.run([sys.executable, "-c", "import scan; scan.scan = lambda p: ('clean', ''); import rescan_held, sys;"
                    " c = rescan_held.main(); print(c); sys.exit(1 if c['failed'] else 0)"],
                   cwd=HERE, env={**os.environ, **ENV}, capture_output=True, text=True, timeout=120)
check("failing folder -> exit 1 and named on stderr", r.returncode == 1 and bad.name in r.stderr)
check("... the other folder was still released", json.loads((good / "WHY.txt").read_text())["state"] == "released")
for p in bad.iterdir():
    p.chmod(0o644)

# held_count: an unreadable folder is skipped, not a reason for 0
h1 = scan.hold(staged("q"), "q.txt", "drop:unknown", "infected", "x", "d" * 64)
locked = scan.hold(staged("r"), "r.txt", "drop:unknown", "infected", "x", "e" * 64)
locked.chmod(0o000)
n = scan.held_count()
locked.chmod(0o755)
check(f"held_count with one unreadable folder = {n} (not 0)", n >= 1)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
