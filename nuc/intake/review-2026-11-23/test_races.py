"""test_races.py - proof for patch_races.py (race review 2026-11-23). Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_races.py

Variables: ROOT (throw-away tree), fails, check(), tmpfile().
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp", "a", "b"):
    (ROOT / d).mkdir()
os.environ.update({"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"),
                   "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "LLM_BASE": "http://x",
                   "AUTH_LEVELS_URL": "http://x", "GOTENBERG_URL": "http://x", "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
                   "INTAKE_PLACE_WAIT": "1"})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import resumable  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


def tmpfile(data: bytes, name="x") -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp"))
    p = d / name
    p.write_bytes(data)
    return p


dig = lambda b: app.hashlib.sha256(b).hexdigest()  # noqa: E731

# R1: a held lock on folder a does not block folder b; on folder a it times out (PLACE_WAIT 1 s)
held = threading.Event()
release = threading.Event()


def hold_a():
    with app._folder_lock(ROOT / "a"):
        held.set()
        release.wait(5)

t = threading.Thread(target=hold_a)
t.start()
held.wait(5)
t0 = time.monotonic()
r = app.place(tmpfile(b"bee"), ROOT / "b", "b.pdf", dig(b"bee"), {})
check(f"R1 another folder files while a is busy ({time.monotonic() - t0:.2f}s)", r["status"] == "filed" and time.monotonic() - t0 < 0.9)
src = tmpfile(b"aaa", "a.pdf")
try:
    app.place(src, ROOT / "a", "a.pdf", dig(b"aaa"), {})
    check("R1 busy folder -> TimeoutError", False)
except TimeoutError:
    check("R1 busy folder -> TimeoutError after PLACE_WAIT", True)
check("R2 bytes moved back to tmp after the failure (no orphan .part)",
      src.exists() and src.read_bytes() == b"aaa" and not any(p.name.endswith(".part") for p in (ROOT / "a").iterdir()))
release.set()
t.join()

# R2: an unreadable file in the folder is skipped during the duplicate scan
(ROOT / "a" / "gone.pdf").write_bytes(b"zzz")
real = app.sha256
app.sha256 = lambda p: (_ for _ in ()).throw(FileNotFoundError("consumed")) if p.name == "gone.pdf" else real(p)
r = app.place(tmpfile(b"zzz"), ROOT / "a", "new.pdf", dig(b"zzz"), {})
app.sha256 = real
check("R2 a file vanishing during the scan does not fail the filing", r["status"] == "filed")

# R5
check("R5 an upload cannot be named like a note", app.safe_name("x.pdf.intake.json") == "x.pdf.intake-json")
f = ROOT / "unsorted" / "bill.pdf"
p1 = app.write_sidecar(f, {"n": 1})
p2 = app.write_sidecar(f, {"n": 2})
check("R5 a second note never overwrites the first", p1 != p2 and json.loads(p1.read_text())["n"] == 1
      and json.loads(p2.read_text())["n"] == 2)

# R6: many threads logging while rotating constantly: no exception, every line lands in some generation
app._LOG_MAX = 300
ths = [threading.Thread(target=lambda i=i: [app.say(f"line {i}-{k}") for k in range(20)]) for i in range(8)]
[x.start() for x in ths]
[x.join() for x in ths]
check("R6 log lock file used", app.LOG.with_name(app.LOG.name + ".lock").exists())

# R3 / R4
resumable._load = lambda uid, email: ({"size": 5}, ROOT / "stage" / f"{uid}.part", ROOT / "stage" / f"{uid}.json")
resumable._who = lambda x: "e"
resumable._FINISHING.add("u1")
r = asyncio.run(resumable.cancel("u1", "e"))
check("R3 cancel while finishing -> 409", r.status_code == 409)
r = asyncio.run(resumable.piece("u1", 0, None, "e"))
check("R4 sequential piece while finishing -> 409", r.status_code == 409)
resumable._FINISHING.discard("u1")
resumable._SEQ_BUSY.add("u2")
r = asyncio.run(resumable.finish("u2", "e"))
check("R4 finish while a sequential piece is written -> 409", r.status_code == 409)
resumable._SEQ_BUSY.discard("u2")


async def boom(uid, x):
    resumable._extra(uid)[1].write_text("lock")
    raise RuntimeError("unexpected")

resumable._finish = boom
try:
    asyncio.run(resumable.finish("u3", "e"))
except RuntimeError:
    pass
check("R3 unexpected error in finish removes the lock", not resumable._extra("u3")[1].exists() and "u3" not in resumable._FINISHING)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
