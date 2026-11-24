"""test_srvA.py - proof for patch_srvA.py (review 2026-11-23 batch A). Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_srvA.py

Checks: #1 same-name race keeps both files; duplicate still detected; #4 missing sentinel refuses, present works;
#13 sidecar name per full file name; #11 null model content -> "unknown"; #3 /upload keeps bytes on a crash;
#9 a second finish while the first runs -> 409; #14 level-check log line has no email; #7 no token -> no start.
Variables: ROOT (throw-away tree), fails, check().
"""
import asyncio
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp", "lib", "nas"):
    (ROOT / d).mkdir()
BASE_ENV = {"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"),
            "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "LLM_BASE": "http://x",
            "AUTH_LEVELS_URL": "http://10.255.255.1/x", "GOTENBERG_URL": "http://x",
            "INTAKE_LOG": str(ROOT / "unsorted" / "log"), "INTAKE_SENTINELS": str(ROOT / "nas" / ".mounted"),
            "SERVICE_KEY": "s-test"}
os.environ.update(BASE_ENV)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import classify  # noqa: E402
import resumable  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


def tmpfile(data: bytes) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "tmp"))
    p = d / "x"
    p.write_bytes(data)
    return p


# #1 race: 8 threads file 8 different "scan.pdf" at once
lib = ROOT / "lib"
datas = [f"content {i}".encode() * 1000 for i in range(8)]
res = []
ts = [threading.Thread(target=lambda b=b: res.append(app.place(tmpfile(b), lib, "scan.pdf", app.hashlib.sha256(b).hexdigest(), {}))) for b in datas]
[t.start() for t in ts]
[t.join() for t in ts]
got = sorted(p.read_bytes() for p in lib.iterdir() if p.is_file())
check("#1 8 same-name files at once -> 8 files, none overwritten", got == sorted(datas))
check("#1 no .part left", not any(p.name.endswith(".part") for p in lib.iterdir()))
r = app.place(tmpfile(datas[0]), lib, "again.pdf", app.hashlib.sha256(datas[0]).hexdigest(), {})
check("#1 byte-identical file -> duplicate", r["status"] == "duplicate")

# #4 sentinel
nas = ROOT / "nas" / "books"
try:
    app.place(tmpfile(b"b"), nas, "b.pdf", app.hashlib.sha256(b"b").hexdigest(), {})
    check("#4 unmounted share refused", False)
except app.NotMounted:
    check("#4 unmounted share refused (and no folder made)", not nas.exists())
(ROOT / "nas" / ".mounted").touch()
r = app.place(tmpfile(b"b"), nas, "b.pdf", app.hashlib.sha256(b"b").hexdigest(), {})
check("#4 mounted share -> filed", r["status"] == "filed")

# #13 sidecar
check("#13 sidecar per full name", app.sidecar_for("/u/bill.pdf").name == "bill.pdf.intake.json"
      and app.sidecar_for("/u/bill.pdf") != app.sidecar_for("/u/bill.docx"))


# #11 null content
class R:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": None}}]}


classify.httpx.post = lambda *a, **k: R()
try:
    check("#11 null model content -> unknown, no exception", classify.ask_model("a.pdf", "text") == "unknown")
except Exception as e:  # noqa: BLE001
    check(f"#11 null model content raised {type(e).__name__}", False)

# #3 /upload keeps the bytes
def boom(*a, **k):
    raise RuntimeError("simulated NAS failure")


app.route_file = boom
from fastapi import UploadFile  # noqa: E402
try:
    asyncio.run(app.upload(file=UploadFile(file=io.BytesIO(b"keep me"), filename="k.pdf"), hint=None, shelf=None,
                           x_auth_email="someone@example.invalid"))
except RuntimeError:
    pass
kept = [p for p in resumable.FAILED.rglob("k.pdf")] if resumable.FAILED.exists() else []
check("#3 /upload crash -> bytes kept in .failed", bool(kept) and kept[0].read_bytes() == b"keep me")

# #9 second finish while the first runs
resumable._load = lambda uid, email: ({"size": 5}, None, None)
resumable._who = lambda x: "e"


async def slow(uid, x):
    await asyncio.sleep(0.5)
    return "first"

resumable._finish = slow


async def both():
    a = asyncio.create_task(resumable.finish("u1", "e"))
    await asyncio.sleep(0.1)
    b = await resumable.finish("u1", "e")
    return await a, b

first, second = asyncio.run(both())
check("#9 second finish while first runs -> 409", first == "first" and getattr(second, "status_code", 0) == 409)
check("#9 set cleared afterwards", "u1" not in resumable._FINISHING)

# #14 no email in the level-check log line
lines = []
app.say = lines.append
app.level_for("someone@example.invalid", "recipe")
check("#14 level-check failure logged without the email", lines and not any("@" in x for x in lines))

# #7 no token -> refuses to start
env = {**os.environ, "INTAKE_TOKEN": ""}
p = subprocess.run([sys.executable, "-c", "import app"], env=env, capture_output=True, text=True,
                   cwd=pathlib.Path(__file__).resolve().parent)
check("#7 empty INTAKE_TOKEN -> start-up error", p.returncode != 0 and "INTAKE_TOKEN is not set" in p.stderr)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
