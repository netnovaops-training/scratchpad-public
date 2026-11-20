#!/usr/bin/env python3
"""Pick up queued documents and read them aloud into the audiobook shelf.

Jason, 2026-09-10: "/listen/ i want to choose what documents go here".

The choosing happens in the search page on the NUC; the reading happens
here, because the voice service binds to 127.0.0.1 on this machine and the
GPU is in it. This is the half that comes and gets the work.

    find (NUC)          this worker (PC)         Audiobookshelf (PC)
    "Read aloud"  --->  claim, fetch, speak ---> M:/audiobooks --> /listen

IT DOWNLOADS THROUGH THE GATED ROUTE, as the person who queued the job. A
queued job is a note saying "fetch this later", so the permission is
checked again at fetch time rather than being inherited from the click.
/file/{id} applies the same owner filter search does, so a job cannot
become a way to read somebody else's document after the fact.

IT SHELLS OUT TO narrate.py rather than importing it. One malformed PDF
then costs one job instead of the worker, and the exact command is in the
log where it can be re-run by hand.

Run it from Task Scheduler every few minutes, or with --loop to sit
running. Either is fine; --once is what a scheduled task wants.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

import httpx

FIND = os.getenv("FIND_URL", "http://192.168.1.86:8087").rstrip("/")
OUT = os.getenv("AUDIOBOOKS", r"M:/audiobooks")
HERE = pathlib.Path(__file__).resolve().parent
NARRATE = HERE / "narrate.py"


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip() or "document"


def run_job(job: dict) -> tuple:
    """(ok, detail, tracks) for one job."""
    owner = job["owner"]
    headers = {"X-Auth-Email": owner}

    with tempfile.TemporaryDirectory() as tmp:
        # Downloaded under its REAL filename, because narrate.py names the
        # output folder from the file's stem. A temp name would produce a
        # shelf full of folders called tmp8f3a2b.
        src = pathlib.Path(tmp) / safe_name(job["name"])
        try:
            with httpx.stream("GET", f"{FIND}/file/{job['doc_id']}",
                              headers=headers, timeout=300,
                              follow_redirects=True) as r:
                if r.status_code != 200:
                    return False, f"could not fetch it ({r.status_code})", 0
                with src.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except Exception as e:  # noqa: BLE001
            return False, f"download failed: {e}", 0

        if src.stat().st_size == 0:
            return False, "the file came back empty", 0

        voice = str(job.get("voice") or "Mom")
        pages = str(job.get("pages") or "")
        # 2026-11-20: the queue's values are checked before they reach the command line. A voice may not look
        # like an option (leading "-") or hold a control character; pages must be "40-58", the same shape
        # narrate.py accepts, and anything else fails the job with the message narrate.py would have given.
        if voice.startswith("-") or re.search(r"[\x00-\x1f]", voice):
            return False, "that voice name is not allowed", 0
        if pages and not re.fullmatch(r"\d+\s*-\s*\d+", pages.strip()):
            return False, "--pages wants something like 40-58", 0
        cmd = [sys.executable, str(NARRATE), str(src),
               "--voice", voice,
               "--out", OUT, "--yes"]
        if pages:
            cmd += ["--pages", pages]
        log(f"  {' '.join(cmd[1:])}")
        try:
            # argv list, no shell: voice/pages are single arguments to our own narrate script
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=60 * 60 * 8, check=False)
        except subprocess.TimeoutExpired:
            return False, "gave up after eight hours", 0

        tail = (p.stdout or "").strip().splitlines()
        last = tail[-1] if tail else ""
        if p.returncode != 0:
            return False, (p.stderr or last or "narration failed")[-300:], 0
        m = re.search(r"(\d+) written", last)
        return True, last[:200], int(m.group(1)) if m else 0


def tick() -> int:
    try:
        jobs = httpx.get(f"{FIND}/api/narrate/queue", timeout=30).json()["jobs"]
    except Exception as e:  # noqa: BLE001
        log(f"could not reach the queue: {e}")
        return 0
    if not jobs:
        return 0

    done = 0
    for job in jobs:
        # Claim before working. Two workers, or a scheduled run overlapping
        # a slow one, would otherwise narrate the same book twice.
        try:
            c = httpx.post(f"{FIND}/api/narrate/{job['id']}/claim",
                           params={"worker": os.environ.get(
                               "COMPUTERNAME", "pc")}, timeout=30)
            if c.status_code != 200:
                continue
        except Exception as e:  # noqa: BLE001
            log(f"could not claim {job['id']}: {e}")
            continue

        log(f"reading {job['name']!r} for {job['owner']} in "
            f"{job.get('voice') or 'Mom'}")
        started = time.time()
        ok, detail, tracks = run_job(job)
        log(f"  {'done' if ok else 'FAILED'} in "
            f"{(time.time() - started) / 60:.0f}m -- {detail}")
        try:
            httpx.post(f"{FIND}/api/narrate/{job['id']}/done", timeout=30,
                       json={"ok": ok, "detail": detail, "tracks": tracks})
        except Exception as e:  # noqa: BLE001
            # The work is done and the files are on disk; only the status
            # is lost. requeue_stale will not touch it because it is no
            # longer RUNNING on the next successful report... but if this
            # keeps failing the job does come back, and re-running is
            # harmless: narrate.py skips tracks that already exist.
            log(f"  could not report the result: {e}")
        done += 1
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if args.loop:
        log(f"watching {FIND} every {args.interval}s -> {OUT}")
        while True:
            tick()
            time.sleep(args.interval)
    n = tick()
    if not n:
        log("nothing queued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
