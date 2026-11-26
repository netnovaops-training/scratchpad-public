#!/usr/bin/env python3
"""Nightly stag run over the NAS library while the Pictures -> Library move is still going.

Started by the Windows scheduled task "stag-night" at 23:00; stops itself at --until
(07:00): stag_db.py finishes its current batch, frees the GPU and exits. Results stay in
D:\\tools\\stag\\tags.db; every night resumes (known SHA-256s and unchanged paths skipped).

Only tags folders that have stopped changing: each check snapshots (file count, bytes) per
year folder of P:\\Library plus P:\\MobileBackup; a folder whose snapshot matches the
previous check (CHECK_GAP apart) is stable. Stable folders with work left go to stag_db.py
(one process per pass, so the GPU is freed between passes; stag_db asks Ollama to unload
its models before loading and before every batch).

Done when STOP_AFTER checks in a row see nothing changing and nothing left: writes
D:\\tools\\stag\\DONE and deletes the scheduled task.
Pass 1 skips the stability check for 2001-2020: the move job reported those as done.
"""
import argparse
import datetime as dt
import logging
import os
import subprocess
import time

PY = r"D:\tools\stag\venv\Scripts\python.exe"
STAG_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stag_db.py")
LIB, MOBILE = r"P:\Library", r"P:\MobileBackup"
LOG = r"D:\tools\stag\run.log"
STOP_FILE, DONE_FILE = r"D:\tools\stag\STOP", r"D:\tools\stag\DONE"
TASK = "stag-night"
CHECK_GAP = 600  # seconds between snapshots
STOP_AFTER = 2   # quiet checks in a row before calling the library done
MOVED = {str(y) for y in range(2001, 2021)}

log = logging.getLogger("run_full")


def snapshot(folder):
    n = size = 0
    for cur, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in ("#recycle", "@eaDir")]
        for f in files:
            try:
                size += os.stat(os.path.join(cur, f)).st_size
                n += 1
            except OSError:
                pass  # moved mid-walk: folder is not stable anyway
    return n, size


def folders():
    out = [os.path.join(LIB, d) for d in sorted(os.listdir(LIB)) if os.path.isdir(os.path.join(LIB, d))]
    return out + [MOBILE]


def run_pass(roots, until):
    """Tag roots; returns stag_db's exit code (0 ok/stopped, 3 = VRAM blocked all night)."""
    log.info("pass start: %s", ", ".join(os.path.relpath(r, "P:\\") for r in roots))
    cmd = [PY, "-I", "-W", "ignore", STAG_DB, *roots, "--all-dirs", "--batch", "4",
           "--min-free-vram", "3000", "--until", until, "--stop-file", STOP_FILE, "--log", LOG]
    with open(LOG + ".err", "a") as err:
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=err,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
    log.info("pass end rc=%d", rc)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="07:00")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [run_full] %(message)s",
                        handlers=[logging.FileHandler(LOG, encoding="utf-8")])
    h, m = map(int, a.until.split(":"))
    now = dt.datetime.now()
    deadline = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if deadline <= now:
        deadline += dt.timedelta(days=1)
    stop = lambda: dt.datetime.now() >= deadline or os.path.exists(STOP_FILE)
    if os.path.exists(DONE_FILE):
        log.info("library already done (%s exists); nothing to do", DONE_FILE)
        return
    if not os.path.isdir(LIB):
        log.error("%s not reachable (P: not mapped in this logon session?); skipping tonight", LIB)
        return
    log.info("night run start, stops at %s", deadline.strftime("%Y-%m-%d %H:%M"))

    first = [f for f in folders() if os.path.basename(f) in MOVED]
    prev = {f: snapshot(f) for f in folders()}
    done = {}
    if run_pass(first, a.until) == 0 and not stop():
        done.update({f: prev[f] for f in first})
    quiet = 0
    while quiet < STOP_AFTER and not stop():
        time.sleep(CHECK_GAP)
        if stop():
            break
        cur = {f: snapshot(f) for f in folders()}
        changing = [f for f in cur if cur[f] != prev.get(f)]
        todo = [f for f in cur if f not in changing and done.get(f) != cur[f] and cur[f][0] > 0]
        log.info("check: %d folders changing, %d to tag", len(changing), len(todo))
        if todo:
            if run_pass(todo, a.until) == 0 and not stop():
                done.update({f: cur[f] for f in todo})
        quiet = 0 if (changing or todo) else quiet + 1
        prev = cur
    if quiet >= STOP_AFTER:
        open(DONE_FILE, "w").write(dt.datetime.now().isoformat())
        log.info("library done: no new files for %d checks; removing scheduled task %s", STOP_AFTER, TASK)
        subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True)
    else:
        log.info("night run end (deadline or stop file); resumes next night")


if __name__ == "__main__":
    main()
