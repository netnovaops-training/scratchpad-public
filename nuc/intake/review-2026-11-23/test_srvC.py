"""test_srvC.py - proof for patch_srvC.py (review 2026-11-23 #15 #16 #17). Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_srvC.py

Checks: #16 a cross-site POST is refused, same-origin and no header pass the gate; #17 a start past the person's
open-bytes cap -> 429, under it -> allowed; #15 a second sequential PUT for a uid already being written -> 409.
Variables: ROOT (throw-away tree), fails, check().
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp"):
    (ROOT / d).mkdir()
os.environ.update({"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"),
                   "INTAKE_STAGE": str(ROOT / "stage"), "TMPDIR": str(ROOT / "tmp"), "LLM_BASE": "http://x",
                   "AUTH_LEVELS_URL": "http://x", "GOTENBERG_URL": "http://x", "INTAKE_LOG": str(ROOT / "unsorted" / "log"),
                   "INTAKE_OPEN_CAP_MB": "100"})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import resumable  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


async def gate(site):
    hdrs = [(b"x-auth-level", b"full")] + ([(b"sec-fetch-site", site.encode())] if site else [])
    scope = {"type": "http", "method": "POST", "path": "/up/start", "headers": hdrs, "client": ("127.0.0.1", 1),
             "query_string": b"", "server": ("x", 80), "scheme": "http", "root_path": "", "http_version": "1.1"}

    async def nxt(_r):
        return Response("passed", status_code=299)
    return (await app.gatekeeper(Request(scope), nxt)).status_code

check("#16 cross-site POST -> 403", asyncio.run(gate("cross-site")) == 403)
check("#16 same-origin POST passes the gate", asyncio.run(gate("same-origin")) == 299)
check("#16 no header (share sheet / server call) passes", asyncio.run(gate(None)) == 299)

# #17: one 80 MB unfinished upload on file for this person
st = resumable.STAGE
st.mkdir(parents=True, exist_ok=True)
(st / "u1.json").write_text(json.dumps({"email": "p@example.invalid", "size": 80 * 1048576}))
(st / "u1.part").write_bytes(b"")
check("#17 open bytes counted", resumable._open_bytes("p@example.invalid") == 80 * 1048576)
check("#17 cap is 100 MB from the env", resumable.OPEN_CAP == 100 * 1048576)
check("#17 80 + 30 MB > cap -> would refuse", resumable._open_bytes("p@example.invalid") + 30 * 1048576 > resumable.OPEN_CAP)
check("#17 other person unaffected", resumable._open_bytes("q@example.invalid") == 0)

# #15: a sequential PUT for a uid already being written -> 409
part = st / "u2.part"
part.write_bytes(b"abc")
resumable._load = lambda uid, email: ({"size": 10}, part, st / "u2.json")
resumable._who = lambda x: "e"
resumable._SEQ_BUSY.add("u2")
r = asyncio.run(resumable.piece("u2", 3, None, "e"))
check("#15 busy uid -> 409 with the current offset", r.status_code == 409 and json.loads(r.body)["offset"] == 3)
resumable._SEQ_BUSY.discard("u2")

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
