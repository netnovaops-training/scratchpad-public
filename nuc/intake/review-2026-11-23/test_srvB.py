"""test_srvB.py - proof for patch_srvB.py (review 2026-11-23 batch B). Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_srvB.py

Checks: #5 big PDF held (422), big video passes; #6 huge Content-Length -> 413, no room -> 507, normal -> passes
the size gate; #10 busy model -> "unknown:busy" in about MODEL_WAIT, lock released after a failed call;
#12 video without the jellyfin grant -> held under PENDING_ROOT, with it -> filed into the media library.
Variables: ROOT (throw-away tree), fails, check().
"""
import asyncio
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp", "pending", "media"):
    (ROOT / d).mkdir()
os.environ.update({"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"),
                   "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "LLM_BASE": "http://x",
                   "AUTH_LEVELS_URL": "http://x", "GOTENBERG_URL": "http://x", "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
                   "PENDING_ROOT": str(ROOT / "pending"), "INTAKE_MODEL_WAIT": "0.3", "INTAKE_MAX_MB": "100",
                   "INTAKE_QUARANTINE": str(ROOT / "unsorted" / ".quarantine")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import classify  # noqa: E402
import scan  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


def tmpfile(name: str, data: bytes = b"x") -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp"))
    p = d / name
    p.write_bytes(data)
    return p


# #5
scan.ENABLED = True
scan.scan = lambda p: ("skipped", "over the limit")
held_before = len(list(scan.QUARANTINE.glob("*"))) if scan.QUARANTINE.exists() else 0
try:
    app._virus_gate(tmpfile("big.pdf"), "big.pdf", "ab" * 32, "drop:jason", "drop from jason")
    check("#5 big PDF held", False)
except HTTPException as e:
    held_after = len(list(scan.QUARANTINE.glob("*")))
    check("#5 big PDF -> 422 and held in quarantine", e.status_code == 422 and held_after == held_before + 1)
try:
    app._virus_gate(tmpfile("big.mp4"), "big.mp4", "cd" * 32, "drop:jason", "drop from jason")
    check("#5 big video still passes (skipped)", True)
except HTTPException:
    check("#5 big video still passes (skipped)", False)


# #6
async def call_gate(cl, path="/share"):
    scope = {"type": "http", "method": "POST", "path": path, "headers": [(b"content-length", str(cl).encode())],
             "client": ("127.0.0.1", 1), "query_string": b"", "server": ("x", 80), "scheme": "http",
             "root_path": "", "http_version": "1.1"}

    async def nxt(_req):
        return Response("passed", status_code=299)
    return await app.gatekeeper(Request(scope), nxt)

r = asyncio.run(call_gate(5 * 1024 ** 3))
check("#6 5 GB declared on /share -> 413 before reading", r.status_code == 413)
real = app.shutil.disk_usage
app.shutil.disk_usage = lambda p: type("U", (), {"free": 10})()
r = asyncio.run(call_gate(50 * 1048576, "/upload"))
check("#6 no room for 50 MB -> 507", r.status_code == 507)
app.shutil.disk_usage = real
r = asyncio.run(call_gate(1048576, "/upload"))
check("#6 1 MB passes the size gate (next check decides)", r.status_code != 413 and r.status_code != 507)

# #10
classify._MODEL_LOCK.acquire()
t0 = time.monotonic()
out = classify.ask_model("a.pdf", "text")
waited = time.monotonic() - t0
classify._MODEL_LOCK.release()
check(f"#10 busy model -> unknown:busy after {waited:.1f}s", out == "unknown:busy" and waited < 2)


def fail(*a, **k):
    raise classify.httpx.ConnectError("down")

classify.httpx.post = fail
classify.time.sleep = lambda s: None
out = classify.ask_model("a.pdf", "text")
free = classify._MODEL_LOCK.acquire(blocking=False)
if free:
    classify._MODEL_LOCK.release()
check("#10 failed call -> unknown:..., lock released", out.startswith("unknown:") and free)

# #12
scan.ENABLED = False
for sub in ("general", "private"):
    (ROOT / "media" / sub).mkdir()
real_dest = "/srv/media/general"
app.portal.WHO["v@example.invalid"] = "casey"


def route(level):
    app.level_for = lambda email, grant: level if grant == "jellyfin" else None
    seen = {}
    real_place = app.place

    def fake_place(tmp, dest, name, digest, meta):
        seen["dest"] = str(dest)
        return {"status": "filed", "path": str(dest / name)}
    app.place = fake_place
    try:
        app.route_file(tmpfile("clip.mp4"), "clip.mp4", 1, "v@example.invalid", hint="video", shelf="general")
    finally:
        app.place = real_place
    return seen.get("dest", "")

d = route(None)
check("#12 no jellyfin grant -> held under PENDING_ROOT/casey/video", d.startswith(str(ROOT / "pending")) and d.endswith("video"))
d = route("full")
check("#12 jellyfin full -> media library", d == real_dest)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
