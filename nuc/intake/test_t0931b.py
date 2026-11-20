#!/usr/bin/env python3
"""test_t0931b.py - in-process test of the T0931 review fixes (items 7-17, 23), 2026-11-20.

    python3 test_t0931b.py

Needs only the packages in requirements.txt (FastAPI's TestClient talks to the app directly: no server, no
docker, no network). Everything lives in a temp folder. Covers what the sandbox tests cannot do without
root or a restart: ENOSPC on start and on a piece, a damaged re-send of an arrived piece, a restart in the
middle of a finish, a stale lock taken by two finishes at once, idempotent start, the gatekeeper rules,
name safety, healthz sentinels, log rotation and the hourly sweep.
"""
import errno
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="t0931b-"))
for d in ("unsorted", "stage", "tmp"):
    (ROOT / d).mkdir()
os.environ.update({
    "INTAKE_KEY": "k-test", "INTAKE_ALLOWED_PEERS": "10.0.0.5,10.0.0.6,10.0.0.7",
    "INTAKE_SERVICE_PEERS": "10.0.0.6", "INTAKE_TOKEN": "n8n-test", "TELEGRAM_ALLOW": '{"42":"jason"}',
    "INTAKE_STAGE": str(ROOT / "stage"), "INTAKE_UNSORTED": str(ROOT / "unsorted"),
    "INTAKE_LOG": str(ROOT / "unsorted" / "intake.log"), "INTAKE_FAILED": str(ROOT / "failed"),
    "PENDING_ROOT": str(ROOT / "pending"), "TMPDIR": str(ROOT / "tmp"), "INTAKE_RESERVE_MB": "1",
    "INTAKE_MAX_MB": "100", "LLM_BASE": "http://10.255.255.1", "INTAKE_LLM_TIMEOUT": "2",
    "INTAKE_SENTINELS": str(ROOT / "mount" / ".mounted"), "INTAKE_LOG_MAX_MB": "1",
})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app as appmod
import resumable
from fastapi.testclient import TestClient

ok_all = True
ME = "user1@example.com"   # private copy: the real tester, like the other intake tests (public copy shows a placeholder)
HDR = {"X-Auth-Email": ME, "X-Drop-Key": "k-test", "X-Auth-Level": "full"}   # what Caddy adds on the real path


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def client(host="10.0.0.5"):
    return TestClient(appmod.app, client=(host, 5000), raise_server_exceptions=False)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def start(c, blob, name="t.bin", **extra):
    r = c.post("/up/start", json={"name": name, "size": len(blob), "parallel": True, **extra}, headers=HDR)
    return r, (r.json() if r.status_code == 200 else {})


def put(c, uid, i, chunk, blob, sum_=True, data=None):
    p = blob[i * chunk:(i + 1) * chunk] if data is None else data
    h = {**HDR, "Content-Type": "application/octet-stream"}
    if sum_:
        h["X-Piece-Sha256"] = sha(blob[i * chunk:(i + 1) * chunk]) if sum_ is True else sum_
    return c.put(f"/up/{uid}/piece/{i}", content=p, headers=h)


def test_gatekeeper():
    c = client()
    check("item 8: write with level full -> not 403", c.post("/up/start", json={"name": "a", "size": 1}, headers=HDR).status_code == 200)
    h = {**HDR, "X-Auth-Level": "view"}
    check("item 8: write with level view -> 403", c.post("/up/start", json={"name": "a", "size": 1}, headers=h).status_code == 403)
    h = {k: v for k, v in HDR.items() if k != "X-Auth-Level"}
    check("item 8: write with NO level from Caddy's address -> 403", c.post("/up/start", json={"name": "a", "size": 1}, headers=h).status_code == 403)
    h = {**HDR, "X-Auth-Level": "weird"}
    check("item 8: write with an odd level -> 403", c.post("/up/start", json={"name": "a", "size": 1}, headers=h).status_code == 403)
    check("item 8: read with level view -> 200", c.get("/", headers={**HDR, "X-Auth-Level": "view"}).status_code == 200)
    svc = client("10.0.0.6")
    h = {"X-Auth-Email": ME, "X-Drop-Key": "k-test"}
    f = {"file": ("x.bin", b"abc")}
    check("item 8: keyed PC service, no level, POST /upload -> allowed", svc.post("/upload", files=f, headers=h).status_code == 200)
    check("item 8: keyed PC service, POST /up/start -> 403 (exception is /upload only)",
          svc.post("/up/start", json={"name": "a", "size": 1}, headers=h).status_code == 403)
    check("item 8: other peer without level on /upload -> 403", c.post("/upload", files=f, headers=h).status_code == 403)
    check("key: wrong key -> 403", c.get("/healthz", headers={"X-Drop-Key": "nope"}).status_code == 403)
    check("peer: stranger address -> 403", client("10.9.9.9").get("/healthz", headers=HDR).status_code == 403)
    data = {"sender": "42", "source": "telegram"}
    g = {"file": ("n.bin", b"abc")}
    check("item 10: /intake with the drop key only -> 403 once INTAKE_TOKEN is set",
          client("10.0.0.7").post("/intake", data=data, files=g, headers={"X-Drop-Key": "k-test"}).status_code == 403)
    check("item 10: /intake with the wrong token -> 403",
          client("10.0.0.7").post("/intake", data=data, files=g, headers={"X-Intake-Token": "x"}).status_code == 403)
    r = client("10.0.0.7").post("/intake", data=data, files=g, headers={"X-Intake-Token": "n8n-test"})
    check(f"item 10: /intake with its own token -> 200 ({r.status_code})", r.status_code == 200)
    r = client("10.0.0.7").post("/intake", data={"sender": "7", "source": "telegram"}, files=g, headers={"X-Intake-Token": "n8n-test"})
    check("item 10: token ok but sender not on the allow list -> 403 (as before)", r.status_code == 403)
    code = subprocess.run([sys.executable, "-c", "import app"], cwd=pathlib.Path(__file__).resolve().parent,
                          env={k: v for k, v in os.environ.items() if k != "INTAKE_KEY"}, capture_output=True, check=False).returncode
    check("item 7: no INTAKE_KEY -> the app refuses to start", code != 0)


def test_pieces():
    c = client()
    blob = os.urandom(5 * 1048576 + 17)
    r, s = start(c, blob, "pieces.bin")
    uid, ch = s["id"], s["chunk"]
    n = -(-len(blob) // ch)
    check("start parallel 200", r.status_code == 200 and s["parallel"] and n == 2)
    check("item 11: a piece with no checksum -> 428", put(c, uid, 0, ch, blob, sum_=False).status_code == 428)
    check("item 11: nothing was written by the 428", c.get(f"/up/{uid}", headers=HDR).json()["missing"] == [0, 1])
    check("piece 0 clean -> 200", put(c, uid, 0, ch, blob).status_code == 200)
    part = ROOT / "stage" / f"{uid}.part"
    before = hashlib.sha256(part.read_bytes()).hexdigest()
    r = put(c, uid, 0, ch, blob, sum_="0" * 64, data=os.urandom(ch))        # 23: damaged re-send, wrong checksum
    check("23: damaged re-send of an arrived piece -> 422", r.status_code == 422)
    r = put(c, uid, 0, ch, blob, data=b"short")                             # 23: cut-short re-send
    check("23: cut-short re-send of an arrived piece -> 400", r.status_code == 400)
    bad = os.urandom(ch)
    r = put(c, uid, 0, ch, blob, sum_=sha(bad), data=bad)                   # damaged but with ITS OWN matching sum
    check("23: whole other piece with a matching sum is 'already' and NOT written",
          r.status_code == 200 and r.json().get("already") is True)
    check("23: file unchanged by all three re-sends", hashlib.sha256(part.read_bytes()).hexdigest() == before)
    r = put(c, uid, 1, ch, blob)
    check("last piece -> 200", r.status_code == 200)
    r = c.post(f"/up/{uid}/finish", headers=HDR)
    check("finish -> 200 and filed", r.status_code == 200 and r.json()[0]["status"] == "filed")
    path = pathlib.Path(r.json()[0]["path"])
    check("filed sha256 == original", path.exists() and sha(path.read_bytes()) == sha(blob))
    check("a late piece after finish -> 409, not written", put(c, uid, 0, ch, blob).status_code == 409)

    # 23: ENOSPC on start (fallocate) and on a piece (pwrite)
    real_fa, real_pw = os.posix_fallocate, os.pwrite
    parts_before = len(list((ROOT / "stage").glob("*.part")))
    os.posix_fallocate = lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full"))
    r, _ = start(c, blob, "full.bin")
    os.posix_fallocate = real_fa
    check("23: ENOSPC when reserving space at start -> 507", r.status_code == 507)
    check("23: ... and no half-made .part is left behind", len(list((ROOT / "stage").glob("*.part"))) == parts_before)
    r, s = start(c, blob, "full2.bin")
    os.pwrite = lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full"))
    r = put(c, s["id"], 0, s["chunk"], blob)
    os.pwrite = real_pw
    check("23: ENOSPC on a piece -> 507", r.status_code == 507)
    check("23: ... and that piece is not listed as in", 0 in c.get(f"/up/{s['id']}", headers=HDR).json()["missing"])


def test_finish_locks():
    c = client()
    blob = os.urandom(300000)
    r, s = start(c, blob, "lock.bin")
    uid, ch = s["id"], s["chunk"]
    put(c, uid, 0, ch, blob)
    stage = ROOT / "stage"
    lock = stage / f"{uid}.finishing"
    lock.touch()
    r = c.post(f"/up/{uid}/finish", headers=HDR)
    check("23: finish while a fresh lock exists -> 409 working", r.status_code == 409 and r.json().get("working"))
    resumable._clear_finish_locks()                         # what start-up does after a restart mid-finish
    check("23: restart mid-finish: the lock is cleared at start-up", not lock.exists())
    # item 12: an OLD lock taken by two finishes at once -> exactly one files
    lock.touch()
    old = time.time() - resumable.LOCK_STALE - 60
    os.utime(lock, (old, old))
    out = []
    ts = [threading.Thread(target=lambda: out.append(client().post(f"/up/{uid}/finish", headers=HDR))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    codes = sorted(r.status_code for r in out)
    check(f"item 12: two finishes over a stale lock -> {codes} (200+409 or 200+200 from the stored answer)",
          codes in ([200, 409], [200, 200]))
    filed = list((ROOT / "unsorted").glob("lock*.bin"))
    check(f"item 12: filed exactly once ({len(filed)})", len(filed) == 1)
    check("item 12: no .old lock files left over after the sweep",
          (resumable.sweep_stale_files(), not list(stage.glob("*.finishing")))[1])


def test_start_rules():
    c = client()
    _, s1 = start(c, b"x" * 1000, "same.bin", lastModified=1700000000000)
    _, s2 = start(c, b"x" * 1000, "same.bin", lastModified=1700000000000)
    check("item 15: same name/size/lastModified -> the same upload handed back",
          s2.get("id") == s1["id"] and s2.get("resumed") is True)
    _, s3 = start(c, b"x" * 1000, "same.bin", lastModified=1700000000001)
    check("item 15: another lastModified -> a new upload", s3["id"] != s1["id"] and "resumed" not in s3)
    _, s4 = start(c, b"x" * 1000, "same.bin")
    _, s5 = start(c, b"x" * 1000, "same.bin")
    check("item 15: no lastModified -> as before, a new upload each time", s4["id"] != s5["id"])
    for nm, want in (("a\nb.bin", "ab.bin"), ("..", "shared"), ("x\x00y.bin", "xy.bin"), ("a b.bin", "ab.bin"),
                     ("../../etc/passwd", "passwd")):
        r, s = start(c, b"x", nm)
        meta = json.loads((ROOT / "stage" / f"{s['id']}.json").read_text())
        check(f"item 9: name {nm!r} -> {meta['name']!r}", meta["name"] == want)
    check("item 17: a body that is not JSON -> 400 in plain words",
          c.post("/up/start", content=b"nope", headers=HDR).status_code == 400)
    check("CodeQL: a path-like id -> 404", c.get("/up/..%2F..%2Fetc%2Fpasswd", headers=HDR).status_code in (404, 422))
    check("CodeQL: a long id -> 404", c.get("/up/" + "a" * 41, headers=HDR).status_code == 404)
    check("CodeQL: a non-ASCII id -> 404", c.get("/up/%C3%A9%C3%A9", headers=HDR).status_code == 404)
    for i in range(resumable.MAX_OPEN + 1):
        r, _ = start(client(), b"x", f"many{i}.bin")
    check("MAX_OPEN: too many unfinished uploads -> 429", r.status_code == 429)


def test_ops():
    c = client()
    r = c.get("/healthz", headers=HDR)
    check("item 13: healthz fails while a NAS sentinel is missing", r.status_code == 503 and "not mounted" in json.dumps(r.json()))
    (ROOT / "mount").mkdir()
    (ROOT / "mount" / ".mounted").touch()
    r = c.get("/healthz", headers=HDR)
    check("item 13: healthz ok once it is there, and shows the .failed count", r.status_code == 200 and "failed" in r.json())
    # sweep: orphan .part, old .tmp, crashed work folder, crashed tmp dir
    st = ROOT / "stage"
    old = time.time() - 5 * 86400
    orphan = st / "orphanxx.part"
    orphan.write_bytes(b"x")
    tmpf = st / ".meta.abcd.tmp"
    tmpf.write_text("x")
    work = st / "deadbeef.work"
    work.mkdir()
    (work / "keep-me.bin").write_bytes(b"precious")
    crash = ROOT / "tmp" / "intake-crashed"
    crash.mkdir()
    (crash / "also.bin").write_bytes(b"precious2")
    for p in (orphan, tmpf, work, crash):
        os.utime(p, (old, old))
    done = resumable.sweep_stale_files()
    check(f"item 13: sweep removed orphan part + old tmp ({done})", not orphan.exists() and not tmpf.exists())
    kept = [p.name for p in (ROOT / "failed").rglob("*.bin")]
    check(f"item 13: the crashed work/tmp folders were KEPT in .failed, not deleted ({kept})",
          "keep-me.bin" in kept and "also.bin" in kept)
    # item 14: start/finish lines (read BEFORE the rotation test below pushes them out)
    first = (ROOT / "unsorted" / "intake.log").read_text()
    check("item 14: start and finish lines are logged with uid and size",
          "start uid=" in first and "finish uid=" in first and "size=" in first)
    check("item 14: no key or token in the log", "k-test" not in first and "n8n-test" not in first)
    # item 14: log rotation
    appmod._LOG_MAX = 2000
    for i in range(80):
        appmod.say(f"line {i} " + "x" * 50)
    logs = sorted(p.name for p in (ROOT / "unsorted").glob("intake.log*"))
    check(f"item 14: intake.log rotates ({logs})", "intake.log.1" in logs and (ROOT / "unsorted" / "intake.log").stat().st_size < 2500)
    # item 16: one model call at a time
    import classify
    inside, peak = [0], [0]
    lock = threading.Lock()

    def fake_post(*a, **k):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.15)
        with lock:
            inside[0] -= 1
        raise OSError("down")
    real = classify.httpx.post
    real_sleep = classify.time.sleep
    classify.httpx.post, classify.time.sleep = fake_post, lambda s: None
    try:
        ts = [threading.Thread(target=classify.ask_model, args=("a.pdf", "text")) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        classify.httpx.post, classify.time.sleep = real, real_sleep
    check(f"item 16: never more than one model call at once (peak {peak[0]})", peak[0] == 1)


if __name__ == "__main__":
    test_gatekeeper()
    test_pieces()
    test_finish_locks()
    test_start_rules()
    test_ops()
    print("ALL PASS" if ok_all else "SOME FAILED")
    sys.exit(0 if ok_all else 1)
