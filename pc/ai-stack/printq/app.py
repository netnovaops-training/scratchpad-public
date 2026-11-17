"""
The print queue watcher, talking to CUPS instead of Windows.

WHY THIS REPLACED THE WINDOWS SCRIPT
====================================
The Windows path went: Telegram -> queue folder -> PowerShell -> Windows
spooler -> WSD port -> printer. It accepted a job, moved the file to
`printed/`, reported success, and produced no paper. Windows had marked the
printer Offline because its WSD port stopped answering, and WSD does not
recover when the printer wakes up. Nothing in that chain was willing to say
so out loud.

This path is: Telegram -> queue folder -> CUPS -> IPP -> printer. Windows is
not in it at all. No spooler, no WSD, no "Use Printer Offline" checkbox, no
scheduled task, no console window popping up every minute.

IT TELLS YOU WHEN IT FAILS
CUPS reports a real reason ("The printer is not responding",
"media-empty-warning") rather than accepting a job and dropping it. Every one
of those reasons is written into printer-status.json, which is what /print
and /stats read -- so a failure shows up on your phone instead of as silence.

WHAT THIS DELIBERATELY DOES NOT DO
It cannot see prints sent to the printer by anyone else -- your mum printing
from her laptop goes straight to the printer and CUPS never hears about it.
That visibility came from the Windows print log. If you want it back, the
Windows watcher still exists and can run alongside purely as a reporter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CUPS_HOST = os.environ.get("CUPS_HOST", "cups:631")
PRINTER_IP = (os.environ.get("PRINTER_IP") or "").strip()
PRINTER_NAME = os.environ.get("PRINTER_NAME", "brother")
QUEUE = Path(os.environ.get("PRINT_QUEUE", "/transcripts/print-queue"))
STATUS_FILE = Path(os.environ.get("PRINTER_STATUS",
                                  "/transcripts/printer-status.json"))
INTERVAL = int(os.environ.get("PRINT_POLL_SECONDS", "15"))
# Brother printers ship with SNMP v2c enabled and the community string
# "public" -- read-only, and it is what every printer-monitoring tool uses.
# Change it here if you have locked yours down.
SNMP_COMMUNITY = os.environ.get("SNMP_COMMUNITY", "public")

# prtMarkerLifeCount, from the standard Printer-MIB (RFC 3805). This is THE
# lifetime page counter: every page the printer has ever produced, no matter
# who sent it -- your PC, a laptop, a phone over AirPrint, a guest.
#
# That property is the whole point. CUPS only knows about jobs CUPS sent, and
# the Windows print log only ever saw jobs from one PC. The printer's own
# counter sees all of them, which makes it a strictly better source for
# "someone in the house printed" than either.
OID_PAGES = "1.3.6.1.2.1.43.10.2.1.4.1.1"

APPROVED = QUEUE / "approved"
PRINTED = QUEUE / "printed"
FAILED = QUEUE / "failed"

# Jobs we have handed to CUPS and are still waiting on.
#
# WHY THIS FILE EXISTS
# `lp` returning 0 means CUPS ACCEPTED the file. It does not mean anything was
# printed. The job then sits in a queue, goes to the printer, and finishes --
# or is aborted because the printer ran out of paper, or the job was cancelled
# from the printer's own panel. None of that comes back through `lp`.
#
# Reporting "printed" at the moment of acceptance is precisely the lie the
# Windows path told for days: it moved the file to printed\, said success, and
# produced no paper. So we remember the job id, and follow it to the end.
#
# On disk rather than in memory so a restart mid-job does not lose track of it.
JOBS_FILE = QUEUE / "jobs.json"

# IPP job states that mean "this job is over, one way or another" (RFC 8011).
# The other states -- pending, pending-held, processing, processing-stopped --
# all mean it is still in flight.
JOB_TERMINAL = {"completed", "canceled", "aborted"}

# How long to keep asking about a job CUPS no longer recognises before giving
# up on it. cupsd.conf sets PreserveJobHistory Yes and MaxJobs 500, so this
# should never trigger -- but a job we ask about forever is a slow leak.
JOB_GIVE_UP_SECONDS = 3600

# Same list as shared/printing.py, enforced a THIRD time. The queue folder is
# where a file from the internet meets a print system; two of the three checks
# being right is not the same as all three.
ALLOWED = {
    ".pdf", ".txt", ".log", ".md", ".csv",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx",
}


def run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            # stdin closed, ON PURPOSE.
            #
            # If cupsd ever asks for a password, the CUPS tools try to read one
            # from stdin and BLOCK. That is what happened for days: `lp` sat
            # printing "Password for root on cups?" and every print died there
            # while the logs showed nothing wrong. With stdin at /dev/null the
            # prompt gets EOF instantly and lp exits with "Unauthorized" --
            # a message we can read, report and act on, in a second rather
            # than after a two-minute timeout.
            stdin=subprocess.DEVNULL,
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def lp(*args: str) -> tuple[int, str]:
    """A CUPS command against the cups container."""
    return run([args[0], "-h", CUPS_HOST, *args[1:]])


# ---------------------------------------------------------------------------
#  Setting the printer up
# ---------------------------------------------------------------------------

def queue_exists() -> bool:
    code, out = lp("lpstat", "-p", PRINTER_NAME)
    return code == 0 and PRINTER_NAME in out


def add_printer() -> str:
    """Add the printer to CUPS by IP, driverless.

    BY IP, NOT BY DISCOVERY, and that is not a shortcut. CUPS finds printers
    with mDNS/Bonjour, which is multicast -- and multicast does not cross
    Docker Desktop's NAT, so a container will never discover a printer on
    your LAN no matter how long it looks. Naming the address is the only
    thing that works here, and it is more stable anyway.

    `-m everywhere` is IPP Everywhere: CUPS asks the printer what it can do
    instead of needing a driver. The DCP-L2550DW supports AirPrint, which is
    the same protocol, so this works with no Brother driver installed.
    """
    if not PRINTER_IP:
        return ("PRINTER_IP is not set. Put your printer's IP address in .env "
                "- find it on the printer: Menu > Network > WLAN > TCP/IP.")

    uri = f"ipp://{PRINTER_IP}/ipp/print"
    code, out = run(["lpadmin", "-h", CUPS_HOST, "-p", PRINTER_NAME,
                     "-E", "-v", uri, "-m", "everywhere"], timeout=90)
    if code != 0:
        # CUPS requires an administrator to ADD a printer, but not to print to
        # one. Over the network that means a password prompt, and the CUPS
        # command-line tools read passwords from a console -- which a
        # container does not have, so there is no way to answer it from here.
        #
        # Rather than weaken cupsd's config to allow unauthenticated admin
        # from anywhere on the Docker network, this asks you to run one
        # command INSIDE the cups container, where root over the local socket
        # is already trusted. One time, then never again -- the queue lives on
        # the cups_config volume and survives restarts.
        if "unauthorized" in out.lower() or "forbidden" in out.lower():
            return ("CUPS won't let me add the printer remotely (Unauthorized).\n"
                    "Adding a printer needs an admin password, and there's no\n"
                    "console in here to type one into.\n"
                    "\n"
                    "Run this ONCE on the PC - inside the container, where it\n"
                    "is already trusted:\n"
                    "\n"
                    f"  docker compose exec cups lpadmin -p {PRINTER_NAME} -E \\\n"
                    f"      -v {uri} -m everywhere\n"
                    "\n"
                    "Then printing works from then on. If that command also\n"
                    "fails, the printer is unreachable rather than a\n"
                    "permissions problem - check the IP and that it is awake.")
        if "host is down" in out.lower() or "unable to connect" in out.lower():
            return (f"Nothing answered at {PRINTER_IP}:631.\n"
                    "\n"
                    "Work through these in order, on the PC:\n"
                    f"  1. ping {PRINTER_IP}\n"
                    "     No reply = the IP is wrong or the printer is asleep.\n"
                    "     Brother lasers drop off Wi-Fi in deep sleep; press a\n"
                    "     button on it and try again.\n"
                    "  2. ipconfig\n"
                    "     Check your PC is on the SAME subnet. If your PC is\n"
                    f"     192.168.1.x and this says 192.168.68.x, the address\n"
                    "     is wrong - those two are easy to misread for each\n"
                    "     other, and a printer on another subnet is unreachable.\n"
                    "  3. Re-read the IP on the printer itself:\n"
                    "     Menu > Network > WLAN > TCP/IP > IP Address\n"
                    "\n"
                    "Then put the right value in PRINTER_IP in .env and:\n"
                    "  docker compose up -d --force-recreate printq")
        return (f"couldn't add the printer at {PRINTER_IP}: {out[:300]}\n"
                "Check the IP is right and the printer is awake.")
    # -E on its own enables it; make it the default so `lp` needs no -d.
    run(["lpadmin", "-h", CUPS_HOST, "-d", PRINTER_NAME])
    run(["cupsenable", "-h", CUPS_HOST, PRINTER_NAME])
    run(["cupsaccept", "-h", CUPS_HOST, PRINTER_NAME])
    return ""


def ensure_printer() -> str:
    if queue_exists():
        return ""
    return add_printer()


# ---------------------------------------------------------------------------
#  Printing
# ---------------------------------------------------------------------------

def print_file(path: Path) -> tuple[bool, str]:
    if path.suffix.lower() not in ALLOWED:
        return False, f"{path.suffix} is not a printable type"

    # Resolve and confirm it is genuinely inside approved/ before handing it
    # to anything. A symlink or a ..\ in a name must not reach outside.
    try:
        real = path.resolve(strict=True)
        if APPROVED.resolve() not in real.parents:
            return False, "path resolves outside the approved queue"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

    code, out = run(["lp", "-h", CUPS_HOST, "-d", PRINTER_NAME,
                     "-t", path.name, str(real)], timeout=120)
    if code != 0:
        return False, out[:300] or "lp failed with no message"
    return True, out.strip()


# ---------------------------------------------------------------------------
#  Following a job to the end
#
#  Everything below exists to answer one question honestly: did the page come
#  out? `lp` cannot answer it -- it returns as soon as CUPS accepts the file.
# ---------------------------------------------------------------------------

def parse_job_id(lp_output: str) -> str | None:
    """Pull the job id out of `lp`'s reply.

    lp prints exactly:  request id is brother-42 (1 file(s))
    """
    m = re.search(r"request id is (\S+)", lp_output or "")
    return m.group(1) if m else None


def job_attrs(job_id: str) -> dict:
    """Ask CUPS how a job ended up. {} if CUPS no longer knows it."""
    if not shutil.which("ipptool"):
        return {}
    num = job_id.rsplit("-", 1)[-1]
    q = Path("/tmp/job.test")
    q.write_text('{ OPERATION Get-Job-Attributes\n'
                 '  GROUP operation-attributes-tag\n'
                 '  ATTR charset attributes-charset utf-8\n'
                 '  ATTR language attributes-natural-language en\n'
                 '  ATTR uri job-uri $uri\n'
                 '}\n', encoding="utf-8")
    # -tv again. -t alone prints "[PASS]" and hides every attribute, which is
    # the mistake that made the printer look silent for a week.
    code, out = run(["ipptool", "-tv", f"ipp://{CUPS_HOST}/jobs/{num}", str(q)],
                    timeout=20)
    if code != 0:
        return {}
    d: dict = {}
    for line in out.splitlines():
        t = line.strip()
        if "=" not in t or " " not in t:
            continue
        name = t.split(" ", 1)[0]
        val = t.split("=", 1)[-1].strip()
        if name in ("job-state", "job-state-reasons", "job-name"):
            d[name] = val.strip('"')
        elif name in ("job-impressions-completed", "job-media-sheets-completed"):
            try:
                d[name] = int(val.split()[0])
            except (ValueError, IndexError):
                pass
    return d


def load_jobs() -> dict:
    try:
        d = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_jobs(d: dict) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(JOBS_FILE)


def track_job(job_id: str, name: str, stored: Path) -> None:
    d = load_jobs()
    d.setdefault("jobs", {})[job_id] = {
        "name": name,
        "sent": int(time.time()),
        "stored": str(stored),
        "state": "pending",
    }
    save_jobs(d)


def check_jobs(st: dict) -> None:
    """Follow every outstanding job and report the ones that ended.

    Fills in three things the rest of the stack reads:
      jobs_active - how many are still in flight right now
      jobs_done   - the ones that ENDED during this poll, with the outcome.
                    This is what an alert should fire on, because it is the
                    first moment we actually know whether paper came out.
      last_job    - the most recent outcome, kept so /stats can show it long
                    after the poll that produced it.
    """
    d = load_jobs()
    jobs = d.get("jobs") or {}
    st["last_job"] = d.get("last")
    st["jobs_recent"] = d.get("recent") or []
    if not jobs:
        st["jobs_active"] = 0
        return

    now = int(time.time())
    done: list[dict] = []

    for job_id, j in list(jobs.items()):
        a = job_attrs(job_id)
        state = a.get("job-state") or ""

        if state and state not in JOB_TERMINAL:
            j["state"] = state          # pending / processing / stopped
            continue

        if not state:
            # CUPS has forgotten it. Almost always means it completed and the
            # history rolled over. Say "unknown" rather than "printed" -- a
            # confirmation we cannot actually confirm is worth nothing.
            if now - int(j.get("sent") or now) < JOB_GIVE_UP_SECONDS:
                continue
            state, reason, pages = "unknown", "CUPS no longer has this job", None
        else:
            reason = a.get("job-state-reasons") or ""
            pages = a.get("job-impressions-completed")
            if pages is None:
                pages = a.get("job-media-sheets-completed")

        ev = {
            "job": job_id,
            "name": j.get("name"),
            "state": state,
            "ok": state == "completed",
            "pages": pages,
            "seconds": now - int(j.get("sent") or now),
            "reason": reason,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        done.append(ev)

        # A job that did NOT complete has a file sitting in printed/ claiming
        # otherwise. Move it, so the folder never disagrees with reality.
        if not ev["ok"]:
            try:
                p = Path(j.get("stored") or "")
                if p.is_file():
                    p.rename(FAILED / p.name)
                    ev["moved_to_failed"] = True
            except Exception as e:  # noqa: BLE001
                ev["reason"] = f"{ev['reason']} ({type(e).__name__} moving the file)"

        jobs.pop(job_id, None)

    if done:
        d["last"] = done[-1]
        st["last_job"] = done[-1]
        # A ROLLING LIST, not just this poll's events.
        #
        # printq looks every 15 seconds; the printwatch workflow looks every 5
        # minutes. If the only record of a finished job lived in the poll that
        # noticed it, roughly nineteen out of twenty confirmations would be
        # gone before anything read the file -- and the ones that survived
        # would look random, which is worse than none.
        #
        # Keeping the last 20 means a reader can pick up anything it has not
        # seen yet, however coarsely it polls.
        d["recent"] = ([*(d.get("recent") or []), *done])[-20:]

    d["jobs"] = jobs
    save_jobs(d)

    st["jobs_active"] = len(jobs)
    st["jobs_done"] = done
    st["jobs_recent"] = d.get("recent") or []
    for ev in done:
        word = "confirmed" if ev["ok"] else f"did NOT print ({ev['state']})"
        pg = f", {ev['pages']} page(s)" if isinstance(ev["pages"], int) else ""
        print(f"[printq] {ev['name']} {word} after {ev['seconds']}s{pg}"
              + (f" - {ev['reason']}" if ev["reason"] and not ev["ok"] else ""),
              flush=True)


# ---------------------------------------------------------------------------
#  Status
# ---------------------------------------------------------------------------

def read_status() -> dict:
    """What CUPS says about the printer, in the shape the rest of the stack reads.

    Deliberately the SAME JSON as the Windows watcher wrote, so /print,
    /stats and the nightly report all keep working with no changes.
    """
    st: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "printer": PRINTER_NAME,
        "model": None,
        "port": f"ipp://{PRINTER_IP}/ipp/print" if PRINTER_IP else None,
        "state": "Unknown",
        "reachable": False,
        "work_offline": False,
        "queued": 0,
        "queue_stuck_min": None,
        "supplies": [],
        # CUPS only knows about jobs IT sent. Saying so explicitly stops the
        # nightly report implying nobody in the house printed all day.
        "print_log_enabled": False,
        "recent": [],
        "errors": [],
        # Files handed to CUPS during THIS poll. Accepted, not yet printed --
        # the distinction matters, and jobs_done below is the other half.
        "printed_now": [],
        # Jobs that reached a final state during this poll. This is the only
        # honest "it printed" signal in the whole file.
        "jobs_done": [],
        # The last 20 outcomes, kept so a reader polling every 5 minutes does
        # not miss confirmations produced by a loop running every 15 seconds.
        "jobs_recent": [],
        "jobs_active": 0,
        "last_job": None,
        "queue_pending": len(list(APPROVED.glob("*"))) if APPROVED.exists() else 0,
        "source": "cups",
    }

    code, out = lp("lpstat", "-l", "-p", PRINTER_NAME)
    if code != 0:
        st["state"] = "not set up in CUPS"
        st["errors"].append(out[:200])
        return st

    low = out.lower()
    if "is idle" in low:
        st["state"] = "Normal"
        st["reachable"] = True
    elif "now printing" in low or "printing" in low:
        st["state"] = "Printing"
        st["reachable"] = True
    elif "disabled" in low:
        st["state"] = "Paused"

    # printer-state-reasons is where CUPS puts the truth. Unlike Windows, it
    # says WHY -- and it stops claiming things are fine once they are not.
    for line in out.splitlines():
        t = line.strip()
        if not t or t.startswith(f"printer {PRINTER_NAME}"):
            continue
        if any(k in t.lower() for k in
               ("not responding", "unreachable", "offline", "timed out")):
            st["state"] = "Offline"
            st["reachable"] = False
            st["errors"].append(t[:200])
        elif "media-empty" in t.lower() or "out of paper" in t.lower():
            st["state"] = "Paper Out"
        elif "jam" in t.lower():
            st["state"] = "PaperJam"
        elif "door" in t.lower() or "cover" in t.lower():
            st["state"] = "Door Open"
        elif "toner-empty" in t.lower():
            st["supplies"].append({"name": "Toner", "pct": 0})
        elif "toner-low" in t.lower():
            st["supplies"].append({"name": "Toner", "pct": 10})

    code, out = lp("lpstat", "-o", PRINTER_NAME)
    if code == 0:
        st["queued"] = len([l for l in out.splitlines() if l.strip()])

    if shutil.which("ipptool") and PRINTER_IP:
        attrs = printer_attrs()
        st["supplies"] = markers(attrs) or st["supplies"]
        # The lifetime page counter. This is the whole basis for "someone in
        # the house printed": the printer counts EVERY page it produces, no
        # matter who sent it -- your mum's laptop, a phone over AirPrint, a
        # guest. The Windows print log could only ever see jobs from that one
        # PC, so this is strictly better than what it replaced.
        pages = attrs.get("pages")
        if not isinstance(pages, int):
            pages = snmp_pages()      # this Brother needs SNMP for it
        if isinstance(pages, int):
            st["pages_total"] = pages
        if attrs.get("printer-make-and-model"):
            st["model"] = attrs["printer-make-and-model"]
    return st


def printer_attrs() -> dict:
    """Ask the printer about itself over IPP. Returns {} if it won't answer."""
    q = Path("/tmp/attrs.test")
    q.write_text('{ OPERATION Get-Printer-Attributes\n'
                 '  GROUP operation-attributes-tag\n'
                 '  ATTR charset attributes-charset utf-8\n'
                 '  ATTR language attributes-natural-language en\n'
                 '  ATTR uri printer-uri $uri\n'
                 '}\n', encoding="utf-8")
    # -tv, NOT -t. Without the v, ipptool prints the test name and [PASS] and
    # nothing else -- the attributes it received are simply not shown. The
    # query worked perfectly the whole time; this code was reading a page that
    # said "PASS" and looking for toner levels on it.
    code, out = run(["ipptool", "-tv", f"ipp://{PRINTER_IP}/ipp/print", str(q)],
                    timeout=30)
    if code != 0:
        return {}
    attrs: dict = {}
    for line in out.splitlines():
        t = line.strip()
        if "=" not in t or " " not in t:
            continue
        name = t.split(" ", 1)[0]
        val = t.split("=", 1)[-1].strip()
        if name in ("marker-names", "marker-levels", "marker-types",
                    "marker-colors", "marker-high-levels", "marker-low-levels"):
            attrs[name] = [x.strip().strip('"') for x in val.split(",")]
        elif name in ("printer-impressions-completed",
                      "printer-pages-completed",
                      "printer-media-sheets-completed"):
            # Different vendors expose the lifetime counter under different
            # names, so take whichever one this printer actually has rather
            # than insisting on the one the spec prefers.
            try:
                attrs["pages"] = int(val.split()[0])
            except (ValueError, IndexError):
                pass
        elif name in ("printer-make-and-model", "printer-state-reasons",
                      "printer-state", "printer-info"):
            attrs[name] = val.strip('"')
    return attrs


def snmp_pages() -> int | None:
    """The printer's lifetime page count, over SNMP. None if it won't say."""
    if not PRINTER_IP or not shutil.which("snmpget"):
        return None
    code, out = run(["snmpget", "-v2c", "-c", SNMP_COMMUNITY, "-Oqv",
                     "-t", "3", "-r", "1", PRINTER_IP, OID_PAGES], timeout=15)
    if code != 0:
        return None
    try:
        # -Oqv prints the bare value, but be tolerant: some agents still
        # prefix a type, e.g. "Counter32: 14203".
        return int(out.strip().split()[-1])
    except (ValueError, IndexError):
        return None


def markers(attrs: dict) -> list[dict]:
    """Toner levels, from the attributes we already fetched."""
    names = attrs.get("marker-names") or []
    levels = attrs.get("marker-levels") or []
    out = []
    for n, p in zip(names, levels):
        try:
            pct = int(p)
        except (TypeError, ValueError):
            continue
        if 0 <= pct <= 100:
            out.append({"name": n, "pct": pct})
    return out


# ---------------------------------------------------------------------------

def write_status(st: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    # Written whole then renamed: a reader must never catch a half-written
    # file, and os.replace is atomic on the same filesystem.
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    tmp.replace(STATUS_FILE)


def main() -> None:
    for d in (APPROVED, PRINTED, FAILED):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[printq] cups={CUPS_HOST} printer={PRINTER_NAME} ip={PRINTER_IP or 'NOT SET'}",
          flush=True)

    # Say ONCE, at startup, what the printer actually told us about itself.
    #
    # Without this the only way to find out was to run ipptool by hand inside
    # the container -- which means remembering that the shell in here is Linux
    # while the shell you are typing in is PowerShell, and getting the nested
    # quoting right across both. That is a silly thing to ask of anyone, and I
    # got it wrong myself. `docker compose logs printq` now answers it.
    if PRINTER_IP and shutil.which("ipptool"):
        a = printer_attrs()
        if not a:
            print("[printq] the printer did not answer an IPP attribute query - "
                  "toner levels and the page counter will be blank", flush=True)
        else:
            print(f"[printq] model        {a.get('printer-make-and-model', '?')}", flush=True)
            print(f"[printq] state        {a.get('printer-state', '?')}"
                  f"  ({a.get('printer-state-reasons', '?')})", flush=True)
            m = markers(a)
            print(f"[printq] supplies     {m or 'not reported by this printer'}", flush=True)
            pages = a.get("pages")
            if isinstance(pages, int):
                print(f"[printq] page counter {pages}  <- household print alerts "
                      "work: this counts EVERY page, whoever sent it", flush=True)
            else:
                # RETRY BEFORE COMPLAINING.
                #
                # This probe runs the instant the container starts, and a
                # Brother laser in deep sleep takes several seconds to answer
                # SNMP at all. A single attempt reported "unavailable over IPP
                # *and* SNMP" and sent you to change a setting on the printer
                # that was already correct -- the polling loop was reading the
                # counter fine 15 seconds later.
                snmp = snmp_pages()
                for _ in range(4):
                    if isinstance(snmp, int):
                        break
                    time.sleep(5)
                    snmp = snmp_pages()
                if isinstance(snmp, int):
                    print(f"[printq] page counter {snmp} (via SNMP - this printer "
                          "does not publish it over IPP)", flush=True)
                    print("[printq]              counts EVERY page, whoever sent it "
                          "- household print alerts will work", flush=True)
                else:
                    print("[printq] page counter no answer yet after 4 tries over "
                          "20s (IPP and SNMP).", flush=True)
                    print("[printq]              This RETRIES every poll, so a "
                          "sleeping printer sorts itself out.", flush=True)
                    print("[printq]              Check printer-status.json for "
                          "pages_total before changing anything;", flush=True)
                    print("[printq]              only if it is still missing in a "
                          "few minutes, enable SNMP at", flush=True)
                    print(f"[printq]              http://{PRINTER_IP} > Network > "
                          "Protocol > SNMP", flush=True)

    while True:
        st = read_status()

        err = ensure_printer()
        if err:
            st["state"] = "not set up"
            st["errors"].append(err)
            print(f"[printq] {err}", flush=True)
        else:
            for f in sorted(APPROVED.glob("*")):
                if not f.is_file():
                    continue
                ok, msg = print_file(f)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if ok:
                    job_id = parse_job_id(msg)
                    # "sent", not "printed". CUPS has the file; the paper is
                    # still a promise at this point. check_jobs() upgrades it
                    # to a real confirmation, or contradicts it.
                    print(f"[printq] sent {f.name} to CUPS as "
                          f"{job_id or 'an untracked job'} - waiting for it to "
                          "finish", flush=True)
                    st["printed_now"].append({"name": f.name, "ok": True,
                                              "job": job_id, "stage": "sent"})
                    dest = PRINTED / f"{stamp}-{f.name}"
                    f.rename(dest)
                    if job_id:
                        track_job(job_id, f.name, dest)
                else:
                    print(f"[printq] FAILED {f.name}: {msg}", flush=True)
                    st["printed_now"].append({"name": f.name, "ok": False,
                                              "reason": msg})
                    st["errors"].append(f"{f.name}: {msg}")
                    # Into failed/, not back to approved/. A file that cannot
                    # print must not be retried forever -- that turns one bad
                    # document into an endless loop of alerts.
                    f.rename(FAILED / f"{stamp}-{f.name}")

        # AFTER submitting, so a job sent this second is already being
        # followed, and BEFORE writing the status file, so a confirmation
        # never waits an extra poll to reach your phone.
        check_jobs(st)

        st["queue_pending"] = len(list(APPROVED.glob("*")))
        write_status(st)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
