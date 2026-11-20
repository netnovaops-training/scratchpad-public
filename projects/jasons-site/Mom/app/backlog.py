"""Take her question now, answer it whenever the desktop next comes on.

THE PROBLEM THIS SOLVES
-----------------------
The Pi is always on. Your desktop is not -- it is in New York, it hibernates,
and some weeks it is off entirely. She is in Nevada and does not know or care
which of your machines is awake.

Without this, a question asked while the desktop is off fails after a four
minute wait with "the computer didn't wake up". She learns that the app
sometimes just does not work, and she stops using it. That is the actual
failure mode, and it is a product failure rather than a technical one.

So the Pi accepts everything immediately -- voice, photos, text -- writes it to
its own disk, and tells her it has been received. A worker drains the backlog
the moment the desktop is reachable. She gets her answer when she gets it, and
nothing is ever lost or refused.

WHY THE FILES ARE COPIED, NOT REFERENCED
----------------------------------------
Uploads are pruned on a schedule. A job sitting in the backlog for nine days
while you are away would otherwise find its own recording deleted underneath
it. Backlog media lives in its own directory that pruning does not touch until
the job is finished.

ORDER
-----
Oldest first, always. If she asked three things on Tuesday, she gets Tuesday's
answers in the order she asked them -- not newest-first, which would have the
assistant replying to a follow-up before the thing it follows up on.

WAKING
------
The worker does NOT wake the desktop on a timer. Waking a machine every few
minutes to check whether anyone needs it would keep it running permanently and
defeat the point of hibernating it. It watches passively, and wakes only when
there is genuinely something waiting AND the wake budget allows it -- see
`MOM_BACKLOG_WAKE`.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from . import core

BACKLOG_DIR = Path(os.getenv("MOM_BACKLOG_DIR", str(core.DATA_DIR / "backlog")))

# How often the worker looks. Cheap: one HTTP probe of the desktop.
POLL_SECONDS = int(os.getenv("MOM_BACKLOG_POLL", "60"))

# May the worker wake the desktop itself when something is waiting?
#   never   only process when the desktop happens to be on
#   once    wake at most once per MOM_BACKLOG_WAKE_GAP seconds
#   always  wake whenever there is work
WAKE_MODE = os.getenv("MOM_BACKLOG_WAKE", "once").strip().lower()
WAKE_GAP = int(os.getenv("MOM_BACKLOG_WAKE_GAP", "3600"))

MAX_ATTEMPTS = int(os.getenv("MOM_BACKLOG_MAX_ATTEMPTS", "6"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS backlog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL,
    username    TEXT NOT NULL,
    question    TEXT NOT NULL DEFAULT '',
    audio_path  TEXT NOT NULL DEFAULT '',
    image_paths TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    status      TEXT NOT NULL DEFAULT 'waiting',  -- waiting|running|done|failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_backlog_status ON backlog(status, created_at);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)


def _keep(path: str) -> str:
    """Move an upload somewhere pruning will not reach until we are done."""
    if not path:
        return ""
    src = Path(path)
    if not src.exists():
        return ""
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKLOG_DIR / f"{int(time.time())}_{src.name}"
    try:
        shutil.copy2(src, dst)
        return str(dst)
    except Exception as e:  # noqa: BLE001
        core.log("warn", "backlog", f"could not preserve {src.name}: {e}")
        return path


def add(thread_id: int, username: str, question: str = "",
        audio_path: str = "", image_paths: Optional[list] = None) -> int:
    """Accept the question now. Nothing about this can fail her."""
    init()
    kept_audio = _keep(audio_path)
    kept_images = [p for p in (_keep(i) for i in (image_paths or [])) if p]
    with core.tx() as c:
        jid = c.execute(
            "INSERT INTO backlog(thread_id, username, question, audio_path, "
            "image_paths, created_at) VALUES (?,?,?,?,?,?)",
            (thread_id, username, question[:8000], kept_audio,
             json.dumps(kept_images), time.time())).lastrowid
        c.execute("UPDATE threads SET status='waiting' WHERE id=?", (thread_id,))
    core.log("info", "backlog",
             f"queued job {jid} for thread {thread_id} "
             f"({'voice' if kept_audio else 'text'}, {len(kept_images)} photo(s))")
    return int(jid)


def waiting(limit: int = 200) -> list:
    try:
        return core.conn().execute(
            "SELECT * FROM backlog WHERE status='waiting' AND attempts < ? "
            "ORDER BY created_at LIMIT ?", (MAX_ATTEMPTS, limit)).fetchall()
    except Exception:
        return []


def counts() -> dict:
    try:
        rows = core.conn().execute(
            "SELECT status, COUNT(*) n FROM backlog GROUP BY status").fetchall()
        d = {r["status"]: r["n"] for r in rows}
    except Exception:
        d = {}
    d["oldest"] = None
    try:
        r = core.conn().execute(
            "SELECT MIN(created_at) t FROM backlog WHERE status='waiting'").fetchone()
        d["oldest"] = r["t"]
    except Exception:
        pass
    return d


def position(thread_id: int) -> Optional[int]:
    """Where she is in the queue, 1-based. Shown to her so the wait is legible."""
    try:
        r = core.conn().execute(
            "SELECT COUNT(*) n FROM backlog WHERE status='waiting' AND created_at <= "
            "(SELECT created_at FROM backlog WHERE thread_id=? AND status='waiting' "
            " ORDER BY created_at LIMIT 1)", (thread_id,)).fetchone()
        return r["n"] or None
    except Exception:
        return None


def _cleanup(job) -> None:
    """Once a job is done its preserved copies are just disk."""
    paths = [job["audio_path"]] + core.jload(job["image_paths"] or "[]", [])
    for p in paths:
        try:
            f = Path(p)
            if f.exists() and BACKLOG_DIR in f.parents:
                f.unlink()
        except Exception:
            pass


# ================================================================= worker ====

_last_wake = 0.0
_stop = threading.Event()


def _may_wake() -> bool:
    global _last_wake
    if WAKE_MODE == "never":
        return False
    if WAKE_MODE == "always":
        return True
    if time.time() - _last_wake >= WAKE_GAP:
        return True
    return False


def drain(handler, limit: int = 25) -> dict:
    """Process everything waiting, oldest first.

    `handler` is main.handle_turn, passed in rather than imported, because
    importing main from here would be a circular import -- and because it makes
    this testable with a stub.
    """
    global _last_wake
    out = {"done": 0, "failed": 0, "skipped": 0}
    jobs = waiting(limit)
    if not jobs:
        return out

    st = core.backend_status()
    if not st["ready"]:
        if _may_wake() and core.AUTO_WAKE and core.DESKTOP_MAC:
            core.log("info", "backlog",
                     f"{len(jobs)} job(s) waiting -- waking the desktop")
            _last_wake = time.time()
            st = core.ensure_awake()
        if not st["ready"]:
            out["skipped"] = len(jobs)
            return out

    core.log("info", "backlog", f"desktop is up -- draining {len(jobs)} job(s)")
    for job in jobs:
        with core.tx() as c:
            c.execute("UPDATE backlog SET status='running', started_at=?, "
                      "attempts=attempts+1 WHERE id=?", (time.time(), job["id"]))
        try:
            handler(job["thread_id"], job["username"], job["question"],
                    job["audio_path"], core.jload(job["image_paths"] or "[]", []))
            with core.tx() as c:
                c.execute("UPDATE backlog SET status='done', finished_at=? WHERE id=?",
                          (time.time(), job["id"]))
            _cleanup(job)
            out["done"] += 1
        except Exception as e:  # noqa: BLE001
            attempts = job["attempts"] + 1
            final = attempts >= MAX_ATTEMPTS
            with core.tx() as c:
                c.execute("UPDATE backlog SET status=?, last_error=? WHERE id=?",
                          ("failed" if final else "waiting", str(e)[:400], job["id"]))
            out["failed"] += 1
            core.log("error", "backlog", f"job {job['id']} failed: {e}")
            # Stop the whole run if the desktop went away mid-drain; the rest
            # would fail identically and burn their attempt counters for nothing.
            if not core.backend_status()["ready"]:
                core.log("warn", "backlog", "desktop went away -- pausing the drain")
                break
    return out


def start_worker(handler) -> None:
    """One background thread, started once, for the life of the process."""
    init()

    def loop():
        # A short initial delay so the app finishes starting before the first
        # probe -- otherwise the very first log line is a spurious failure.
        time.sleep(20)
        while not _stop.wait(POLL_SECONDS):
            try:
                if waiting(1):
                    drain(handler)
            except Exception as e:  # noqa: BLE001
                core.log("error", "backlog", f"worker: {e}")

    threading.Thread(target=loop, daemon=True, name="backlog").start()
    core.log("info", "backlog",
             f"worker started (every {POLL_SECONDS}s, wake mode '{WAKE_MODE}')")


def stop() -> None:
    _stop.set()
