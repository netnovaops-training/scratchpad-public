"""
The print server's brain: everything that decides, nothing that touches a
printer.

WHY THE LOGIC IS HERE AND NOT IN THE POWERSHELL
===============================================
Three surfaces need to agree about printing: the Telegram bot (which accepts
a file and asks you to approve it), the n8n alert workflow (which tells you
what happened), and the Windows watcher (which does the actual printing).
Anything they all have to agree on -- what counts as a safe file, what counts
as low toner, how a job is described in a message -- lives here, once, as
pure functions over plain data. No printer, no filesystem, no network, so it
is all testable on a machine with no printer attached.

THE SECURITY PART, WHICH IS THE WHOLE POINT OF ALLOWED_EXTENSIONS
=================================================================
The Windows watcher prints by handing a file to Windows' "print" verb. That
verb is a shell action: for a .pdf it opens the PDF handler and prints, and
for a .ps1 or .bat or .lnk it can end up EXECUTING the thing instead.

So the queue folder is a place where a file arriving from the internet gets
handed to the Windows shell. That is exactly the shape of a remote code
execution bug, and the only thing standing in front of it is this list. It is
an ALLOWLIST, not a blocklist, deliberately: a blocklist is wrong the moment
Windows adds a new executable file type, and it already has more of those
than anyone can remember (.scr, .cpl, .msc, .jse, .wsf, .msi, .reg...).

Double extensions are the classic bypass -- "invoice.pdf.exe" reads as a PDF
to a human and runs as a program on Windows. is_printable() checks the LAST
extension only, which is the one Windows actually acts on.
"""

from __future__ import annotations

import re

# Everything Windows can print from a shell "print" verb with an ordinary
# document handler, and nothing that can execute. If you add to this, ask
# "can this file type run code" first -- and note that Office macro formats
# (.docm, .xlsm) are deliberately absent for that reason.
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".txt", ".log", ".md", ".csv",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".doc", ".docx", ".rtf", ".odt",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx",
}

# 20 MB, because that is a HARD Telegram limit, not a preference: a bot can
# only download files up to 20 MB through the cloud Bot API, no matter what
# the sender's own limit is (2 GB for a normal account, 4 GB with Premium).
#
# So a 22 MB PDF would be accepted here, then fail at download with Telegram's
# own "file is too big" -- an error that arrives AFTER the confirmation and
# names no cause. Rejecting at the real limit, with the real reason, is the
# difference between "this can't work" and "something went wrong".
#
# Raising this requires running a local Bot API server, which is a whole
# extra service; splitting or compressing the PDF is almost always easier.
MAX_PRINT_BYTES = 20 * 1024 * 1024

# Above this many pages, say so loudly in the confirmation. Not a block --
# sometimes you really do mean to print a 200-page manual -- but "12 pages"
# and "212 pages" should not look the same on a phone screen at a glance.
BIG_JOB_PAGES = 20

# Toner/ink thresholds, in percent remaining.
TONER_WARN_PCT = 20
TONER_CRITICAL_PCT = 8


def file_extension(name: str) -> str:
    """The LAST extension, lowercased. 'a.pdf.exe' -> '.exe'."""
    name = (name or "").strip().rstrip(".")
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[1].lower()


def is_printable(name: str) -> bool:
    return file_extension(name) in ALLOWED_EXTENSIONS


def safe_name(name: str) -> str:
    """A filename that cannot escape the queue folder or confuse Windows.

    Strips any directory component, anything Windows forbids in a name, and
    the reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) which
    behave as devices rather than files no matter what folder they are in.
    """
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return "document"
    stem = name.rsplit(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(COM|LPT)[1-9]", stem):
        name = "_" + name
    return name[:120]


def reject_reason(name: str, size_bytes: int) -> str:
    """Why this file can't be printed, or '' if it can."""
    ext = file_extension(name)
    if not ext:
        return ("That file has no extension, so Windows wouldn't know how to "
                "print it.")
    if ext not in ALLOWED_EXTENSIONS:
        return (f"Won't print {ext} files.\n"
                "Only documents and images are allowed - anything that could "
                "run code is refused on purpose, because this folder gets "
                "handed straight to Windows.\n"
                "Allowed: pdf, txt, csv, md, jpg, png, doc(x), xls(x), ppt(x), rtf, odt")
    if size_bytes > MAX_PRINT_BYTES:
        return (f"That's {size_bytes / 1024 / 1024:.1f} MB, and Telegram won't "
                f"let a bot download anything over "
                f"{MAX_PRINT_BYTES // 1024 // 1024} MB.\n"
                "That's Telegram's limit, not mine - you can SEND bigger files, "
                "but I can't fetch them.\n"
                "Split the PDF, or print it in halves.")
    return ""


def describe_job(name: str, size_bytes: int, pages: int | None = None) -> str:
    """The confirmation text: what you're about to spend paper on."""
    size = (f"{size_bytes / 1024 / 1024:.1f} MB" if size_bytes >= 1024 * 1024
            else f"{max(1, size_bytes // 1024)} KB")
    lines = [f"{name}", f"  {size}"]
    if pages is not None:
        lines[-1] += f"  -  {pages} page{'s' if pages != 1 else ''}"
        if pages >= BIG_JOB_PAGES:
            lines.append(f"  THAT IS {pages} PAGES. Check before you confirm.")
    elif file_extension(name) == ".pdf":
        lines.append("  (couldn't read the page count)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Printer state
# ---------------------------------------------------------------------------

def toner_problems(supplies: list[dict] | None) -> list[str]:
    """Low-toner lines, worst first. Empty means every cartridge is fine."""
    out: list[tuple[int, str]] = []
    for s in supplies or []:
        pct = s.get("pct")
        if not isinstance(pct, (int, float)):
            continue
        name = s.get("name") or "toner"
        if pct <= TONER_CRITICAL_PCT:
            out.append((int(pct), f"{name} almost empty ({int(pct)}%)"))
        elif pct <= TONER_WARN_PCT:
            out.append((int(pct), f"{name} low ({int(pct)}%)"))
    out.sort()
    return [t for _, t in out]


def printer_problems(status: dict | None) -> list[str]:
    """Everything wrong with the printer right now, worst first."""
    if not status:
        return ["no printer status recorded - is the print server running?"]

    out: list[str] = []

    # "Windows says offline" and "the printer is off" look identical in the
    # status and are completely different problems. Windows sets this flag
    # itself when a port stops answering and does NOT clear it when the
    # printer comes back -- so the printer can be switched on, on the network,
    # and still have every job queue silently behind this one checkbox.
    if status.get("work_offline"):
        port = str(status.get("port") or "")
        msg = ("WINDOWS has marked this printer offline - the printer itself "
               "may be perfectly fine.\n"
               "     Fix now:  Settings > Bluetooth & devices > Printers >\n"
               "               this printer > Printer properties >\n"
               "               untick 'Use Printer Offline'")
        if port.upper().startswith("WSD"):
            msg += ("\n     It is on a WSD port, which is the usual cause. WSD\n"
                    "     drops Brother printers when they sleep and does not\n"
                    "     recover. The durable fix is a Standard TCP/IP port\n"
                    "     pointed at the printer's IP address.")
        out.append(msg)
    elif status.get("reachable") is False:
        out.append("PRINTER UNREACHABLE - it's off, asleep, or off the network")

    # These are Get-Printer's PrinterStatus names, which is a fixed vocabulary.
    #
    # Previously this matched a state string produced by hand-decoding a WMI
    # enum -- and the table used was the WRONG enum, so a healthy printer
    # reported "Jammed". Anything not recognised now says exactly that
    # instead of being guessed at, because a wrong word here trains you to
    # ignore the alert that eventually matters.
    #
    # Normal, Idle, Printing, Processing and Warming Up are all fine and
    # deliberately absent: alerting on "Printing" would fire on every job.
    state = (status.get("state") or "").strip()
    key = state.lower().replace(" ", "").replace("-", "").replace("_", "")

    FAULTS = {
        "paperjam": "PAPER JAM",
        "paperout": "OUT OF PAPER",
        "papererror": "paper problem",
        "paperproblem": "paper problem",
        "outofpaper": "OUT OF PAPER",
        "dooropen": "a door or cover is open",
        "offline": "printer is OFFLINE - asleep, off, or off the network",
        "notavailable": "printer not available",
        "error": "printer reports an ERROR",
        "outofmemory": "printer out of memory",
        "userintervention": "needs someone to press something",
        "servicerequested": "printer is asking for service",
        "tonerlow": "toner low",
        "nottoner": "out of toner",
        "outputbinfull": "output tray is full",
        "paused": "printing is PAUSED in Windows",
    }
    # A flags enum can report several at once ("Offline, PaperOut").
    for part in [p.strip().lower().replace(" ", "") for p in state.split(",")] or [key]:
        # "offline" is already reported above, with the actual fix attached.
        if part == "offline" and status.get("work_offline"):
            continue
        if part in FAULTS:
            out.append(FAULTS[part])

    out.extend(toner_problems(status.get("supplies")))

    stuck = status.get("queue_stuck_min")
    if isinstance(stuck, (int, float)) and stuck >= 10:
        out.append(f"a job has been stuck in the queue for {int(stuck)} min")

    return out


def format_printer_status(status: dict | None) -> str:
    """The /print status screen. Problems first, same as /health."""
    if not status:
        return ("No printer status yet.\n"
                "  printserver.cmd -Install   sets up the watcher")

    L: list[str] = []
    probs = printer_problems(status)
    if probs:
        L.append("PROBLEMS")
        for p in probs:
            L.append("  ! " + p)
    else:
        L.append("Printer is ready.")
    L.append("")

    L.append(status.get("printer") or "(no printer name)")
    if status.get("model"):
        L.append(f"  model    {status['model']}")
    if status.get("state"):
        L.append(f"  state    {status['state']}")
    if status.get("queued") is not None:
        L.append(f"  queue    {status['queued']} job(s) waiting")
    if status.get("pages_total") is not None:
        L.append(f"  lifetime {status['pages_total']} pages")

    sup = status.get("supplies") or []
    if sup:
        L.append("")
        L.append("Supplies")
        for s in sup:
            pct = s.get("pct")
            if isinstance(pct, (int, float)):
                filled = max(0, min(10, int(pct) // 10))
                bar = "#" * filled + "." * (10 - filled)
                L.append(f"  {(s.get('name') or '?'):<14} {bar} {int(pct):>3}%")
            else:
                L.append(f"  {(s.get('name') or '?'):<14} (level not reported)")

    jobs = status.get("recent") or []
    if jobs:
        L.append("")
        L.append("Recent jobs")
        for j in jobs[:6]:
            L.append("  " + format_job_line(j))

    age = status.get("age_min")
    if isinstance(age, (int, float)) and age > 15:
        L.append("")
        L.append(f"This is {int(age)} min old - the print server may have stopped.")
        L.append("  printserver.cmd -Status")
    return "\n".join(L)


def format_job_line(j: dict) -> str:
    """One completed print job, on one line, readable on a phone."""
    when = (j.get("time") or "")[11:16]          # HH:MM out of an ISO stamp
    who = j.get("user") or "?"
    doc = (j.get("document") or "?")
    if len(doc) > 38:
        doc = doc[:35] + "..."
    pages = j.get("pages")
    bits = [when, who, doc]
    if isinstance(pages, int):
        bits.append(f"{pages}p")
    if j.get("status") and j["status"].lower() not in ("printed", "ok", "success"):
        bits.append(f"[{j['status']}]")
    return "  ".join(b for b in bits if b)


def new_jobs(previous_ids: list[str] | set[str] | None,
             jobs: list[dict]) -> list[dict]:
    """Jobs not seen last time.

    Edge-triggered, exactly like the container heartbeat: alerting on the
    CURRENT state every two minutes would text you about the same print job
    forever. The id has to be stable across polls, so it is built from the
    timestamp plus the job id rather than from position in the list.
    """
    seen = set(previous_ids or [])
    return [j for j in jobs if job_key(j) not in seen]


def job_key(j: dict) -> str:
    return f"{j.get('time', '')}|{j.get('job_id', '')}|{j.get('document', '')}"


def format_job_alert(j: dict, me: str = "") -> str:
    """The Telegram message for one completed job.

    Names the person, because "someone in the house printed something" is the
    thing being asked for -- and if it was you, says so, so a print you
    started from your phone doesn't read as an intruder.
    """
    who = j.get("user") or "someone"
    mine = me and who.lower().endswith(me.lower())
    head = "you printed" if mine else f"{who} printed"
    doc = j.get("document") or "a document"
    pages = j.get("pages")
    line = f"{head}: {doc}"
    if isinstance(pages, int):
        line += f" ({pages} page{'s' if pages != 1 else ''})"
    secs = j.get("seconds")
    if isinstance(secs, (int, float)) and secs > 0:
        line += f", took {int(secs)}s"
    if j.get("printer"):
        line += f"\non {j['printer']}"
    return line
