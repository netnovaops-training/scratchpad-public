"""Outbound email with a swappable transport and a local-file fallback.

WHY IT IS BUILT THIS WAY
------------------------
The destination addresses are Proton (@pm.me), but Proton cannot be the SENDER
on a free plan. Verified July 2026:

  * Free @pm.me addresses are RECEIVE-ONLY.
  * Proton SMTP submission tokens require a PAID plan AND a custom domain --
    you cannot get a token for an @pm.me or @proton.me address.
  * Proton Mail Bridge requires a paid plan and does not officially support
    ARM64, so it cannot run on a Raspberry Pi 5 at all.

None of that stops mail being DELIVERED to Proton. So the sender is pluggable:
Gmail with an app password works free today, and if a paid Proton plan with a
custom domain ever appears, changing three environment variables switches it
over with no code change.

Every message is ALSO written to disk before any send is attempted. If email
breaks -- expired app password, no internet, Google changing the rules -- the
documentation still exists and the dashboard still shows it. Email is a
convenience here, never the system of record.
"""
import os
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Optional

from . import core

# --- transport presets -------------------------------------------------------
# Selected with MOM_SMTP_PRESET. "custom" uses the raw host/port/TLS values.
PRESETS = {
    "gmail":  {"host": "smtp.gmail.com",       "port": 587, "tls": "starttls"},
    "proton": {"host": "smtp.protonmail.ch",   "port": 587, "tls": "starttls"},
    "mailjet": {"host": "in-v3.mailjet.com",   "port": 587, "tls": "starttls"},
    "brevo":  {"host": "smtp-relay.brevo.com", "port": 587, "tls": "starttls"},
    "custom": {},
}

PRESET = os.getenv("MOM_SMTP_PRESET", "gmail").strip().lower()
HOST = os.getenv("MOM_SMTP_HOST", "") or PRESETS.get(PRESET, {}).get("host", "")
PORT = int(os.getenv("MOM_SMTP_PORT", "0") or PRESETS.get(PRESET, {}).get("port", 587))
TLS = (os.getenv("MOM_SMTP_TLS", "") or PRESETS.get(PRESET, {}).get("tls", "starttls")).lower()
USER = os.getenv("MOM_SMTP_USER", "")
PASSWORD = os.getenv("MOM_SMTP_PASSWORD", "")
FROM_ADDR = os.getenv("MOM_SMTP_FROM", "") or USER
FROM_NAME = os.getenv("MOM_SMTP_FROM_NAME", "Tech Help")
TO_ADDRS = [a.strip() for a in os.getenv("MOM_MAIL_TO", "").split(",") if a.strip()]
ENABLED = bool(HOST and USER and PASSWORD and TO_ADDRS)
TIMEOUT = int(os.getenv("MOM_SMTP_TIMEOUT", "30"))
MAX_ATTEMPTS = int(os.getenv("MOM_MAIL_MAX_ATTEMPTS", "5"))

MAIL_DIR = core.DATA_DIR / "mail"


def status() -> dict:
    return {
        "enabled": ENABLED, "preset": PRESET, "host": HOST, "port": PORT,
        "tls": TLS, "user": USER, "from": FROM_ADDR, "to": TO_ADDRS,
        "reason": ("" if ENABLED else
                   "Set MOM_SMTP_USER, MOM_SMTP_PASSWORD and MOM_MAIL_TO to enable."),
    }


def _write_to_disk(subject: str, body: str, ticket_no: str = "") -> Path:
    MAIL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    safe = "".join(c for c in (ticket_no or subject) if c.isalnum() or c in "-_")[:50]
    path = MAIL_DIR / f"{stamp}_{safe or 'message'}.md"
    path.write_text(f"# {subject}\n\n{body}\n", encoding="utf-8")
    return path


def queue(subject: str, body_md: str, ticket_id: Optional[int] = None,
          ticket_no: str = "") -> int:
    """Write the document to disk, record it in the outbox, and try to send.

    Returns the outbox row id. Never raises -- a failed send is a logged
    condition, not an exception that should abort closing a ticket.
    """
    path = _write_to_disk(subject, body_md, ticket_no)
    with core.tx() as c:
        oid = c.execute(
            "INSERT INTO outbox(created_at, subject, body_md, to_addrs, status, "
            "attempts, ticket_id, file_path) VALUES (?,?,?,?,?,0,?,?)",
            (time.time(), subject, body_md, ",".join(TO_ADDRS),
             "queued" if ENABLED else "disabled", ticket_id, str(path))).lastrowid
    core.log("info", "mail", f"Queued '{subject}' -> {path.name}")
    if ENABLED:
        threading.Thread(target=_attempt, args=(int(oid),), daemon=True).start()
    return int(oid)


def _attempt(outbox_id: int) -> None:
    row = core.conn().execute("SELECT * FROM outbox WHERE id=?", (outbox_id,)).fetchone()
    if not row or row["status"] == "sent":
        return
    ok, err = send_now(row["subject"], row["body_md"],
                       [a for a in (row["to_addrs"] or "").split(",") if a])
    with core.tx() as c:
        if ok:
            c.execute("UPDATE outbox SET status='sent', sent_at=?, attempts=attempts+1, "
                      "last_error='' WHERE id=?", (time.time(), outbox_id))
        else:
            c.execute("UPDATE outbox SET status='failed', attempts=attempts+1, "
                      "last_error=? WHERE id=?", (err[:600], outbox_id))
    core.log("info" if ok else "error", "mail",
             f"outbox {outbox_id}: {'sent' if ok else err[:200]}")


def send_now(subject: str, body_md: str, to: Optional[list] = None) -> tuple[bool, str]:
    """Send one message. Returns (ok, error). Never raises."""
    to = to or TO_ADDRS
    if not ENABLED:
        return False, "email not configured"
    if not to:
        return False, "no recipients"

    msg = EmailMessage()
    msg["From"] = formataddr((FROM_NAME, FROM_ADDR))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=FROM_ADDR.split("@")[-1] or "localhost")
    msg.set_content(body_md)
    # A very light HTML part so it is readable in a mail client without a
    # markdown renderer. Deliberately minimal -- no images, no tracking.
    msg.add_alternative(_md_to_html(body_md), subtype="html")

    try:
        ctx = ssl.create_default_context()
        if TLS == "ssl":
            with smtplib.SMTP_SSL(HOST, PORT, timeout=TIMEOUT, context=ctx) as s:
                s.login(USER, PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(HOST, PORT, timeout=TIMEOUT) as s:
                s.ehlo()
                if TLS == "starttls":
                    s.starttls(context=ctx)
                    s.ehlo()
                s.login(USER, PASSWORD)
                s.send_message(msg)
        return True, ""
    except smtplib.SMTPAuthenticationError as e:
        return False, (f"Authentication rejected. For Gmail this means the app password "
                       f"is wrong or was revoked (they are revoked whenever you change "
                       f"your Google password). Detail: {e}")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def retry_failed() -> int:
    """Re-attempt anything that failed but has attempts left. Called hourly."""
    if not ENABLED:
        return 0
    rows = core.conn().execute(
        "SELECT id FROM outbox WHERE status='failed' AND attempts < ? "
        "ORDER BY created_at LIMIT 20", (MAX_ATTEMPTS,)).fetchall()
    for r in rows:
        _attempt(r["id"])
        time.sleep(1)
    return len(rows)


def list_outbox(limit: int = 60):
    return core.conn().execute(
        "SELECT * FROM outbox ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


_HTML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _md_to_html(md: str) -> str:
    """Deliberately tiny. Mail clients strip most CSS anyway."""
    out, in_list = [], False
    for raw in md.split("\n"):
        line = "".join(_HTML_ESC.get(ch, ch) for ch in raw.rstrip())
        import re
        line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            if in_list:
                out.append("</ul>"); in_list = False
            lvl = min(len(h.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{h.group(2)}</h{lvl}>")
            continue
        li = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$", line)
        if li:
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{li.group(1)}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        if not line.strip():
            continue
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            out.append("<hr>")
        else:
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return ("<html><body style=\"font:15px/1.6 -apple-system,Segoe UI,Roboto,"
            "sans-serif;color:#1a1a1a;max-width:640px\">" + "\n".join(out) +
            "</body></html>")
