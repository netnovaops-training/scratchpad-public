#!/usr/bin/env python3
"""Receive tickets from the Pi and write them to this computer.

RUN THIS ON THE DESKTOP
-----------------------

    set MOM_WEBHOOK_SECRET=<the same secret as the Pi's .env>
    python desktop-listener.py

    python desktop-listener.py --dir "C:\\TechHelp" --port 8787

Every ticket the Pi closes arrives here as JSON and is written to

    <dir>\\<YYYY-MM>\\<TICKET-NO>\\ticket.json
                                 \\summary.txt      (readable version)
                                 \\photo-1.jpg      (what she actually sent)

STANDARD LIBRARY ONLY
---------------------
No pip install, nothing to keep updated, no dependency that can break in a year.
It is one file and it will still run on whatever Python you have.

WHY THE SIGNATURE MATTERS
-------------------------
This listens on your LAN. Without verification, anything else on the network
could post fabricated tickets into your archive and you would have no way to
tell a real record from an invented one. Every request must carry

    X-TechHelp-Signature: sha256=<hmac of the exact body>

computed with the shared secret. Mismatches are refused with a 401 and logged.
The check uses compare_digest so it cannot be probed by timing.

If MOM_WEBHOOK_SECRET is unset this refuses to start, rather than quietly
accepting anything -- an unauthenticated listener is worse than none, because
it looks like it is working.

FIREWALL
--------
Windows will ask whether to allow Python through. Allow it on PRIVATE networks
only. This never needs to be reachable from the internet, and it must never be
port-forwarded.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SECRET = os.environ.get("MOM_WEBHOOK_SECRET", "").strip()
OUT = Path(os.environ.get("MOM_TICKET_DIR", "TechHelp-Tickets"))
MAX_BODY = 64 * 1024 * 1024      # 64 MB: a few phone photos, not a disk image


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def verify(body: bytes, header: str) -> bool:
    if not header:
        return False
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    # Constant time: a plain == leaks how much of the signature was right.
    return hmac.compare_digest(expected, header.strip())


def readable(t: dict) -> str:
    lines = [f"{t.get('ticket_no', '?')}  --  {t.get('title', '')}", ""]
    lines.append(f"Status:   {t.get('status', '')}")
    lines.append(f"Category: {t.get('category', '')}")
    lines.append(f"For:      {t.get('username', '')}")
    lines.append(f"Opened:   {t.get('opened_local', '')}")
    lines.append("")
    if t.get("summary"):
        lines += ["WHAT HAPPENED", "", t["summary"], ""]
    if t.get("root_cause"):
        lines += ["CAUSE", "", t["root_cause"], ""]
    if t.get("steps"):
        lines += ["STEPS THAT WORKED", ""]
        lines += [f"  {i}. {s}" for i, s in enumerate(t["steps"], 1)]
        lines.append("")
    if t.get("models_used"):
        lines += ["MODELS", ""]
        for m in t["models_used"]:
            state = "ok" if m.get("ok") else "FAILED"
            lines.append(f"  {m.get('role','?'):8} {m.get('model','?'):26} "
                         f"{(m.get('ms') or 0)/1000:.1f}s  {state}")
        lines.append("")
    lines += ["CONVERSATION", ""]
    for m in t.get("messages", []):
        who = "SHE ASKED" if m.get("role") == "user" else "ANSWER"
        lines += [f"--- {who} ---", m.get("text", ""), ""]
    return "\n".join(lines)


def save(t: dict) -> Path:
    no = (t.get("ticket_no") or "UNKNOWN").replace("/", "-").replace("\\", "-")
    month = datetime.now().strftime("%Y-%m")
    d = OUT / month / no
    d.mkdir(parents=True, exist_ok=True)

    photos = t.get("photos") or []
    # Keep the JSON readable by pulling the base64 blobs out of it; the images
    # are written as real files next to it instead.
    slim = dict(t)
    slim["photos"] = [{k: v for k, v in p.items() if k != "b64"} for p in photos]
    (d / "ticket.json").write_text(json.dumps(slim, indent=2), encoding="utf-8")
    (d / "summary.txt").write_text(readable(t), encoding="utf-8")

    for i, p in enumerate(photos, 1):
        if not p.get("b64"):
            continue
        ext = Path(p.get("name", "photo.jpg")).suffix or ".jpg"
        try:
            (d / f"photo-{i}{ext}").write_bytes(base64.b64decode(p["b64"]))
        except Exception as e:
            log(f"  could not write photo {i}: {e}")
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = "TechHelpListener/1"

    def log_message(self, *a):
        pass                       # our own logging is friendlier

    def _reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._reply(200, {"ok": True, "service": "tech-help-listener",
                          "writing_to": str(OUT.resolve())})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._reply(413, {"error": "bad length"})
            return
        body = self.rfile.read(length)

        if not verify(body, self.headers.get("X-TechHelp-Signature", "")):
            log(f"REFUSED unsigned or badly-signed request from {self.client_address[0]}")
            self._reply(401, {"error": "bad signature"})
            return

        try:
            t = json.loads(body)
        except Exception:
            self._reply(400, {"error": "not JSON"})
            return

        if t.get("status") == "test":
            log("Connection test received -- the link works.")
            self._reply(200, {"ok": True, "test": True})
            return

        try:
            d = save(t)
        except Exception as e:
            log(f"could not save: {e}")
            self._reply(500, {"error": str(e)})
            return

        n = len([p for p in (t.get("photos") or []) if p.get("b64")])
        log(f"{t.get('ticket_no','?')}  {t.get('title','')[:44]}"
            + (f"  ({n} photo{'s' if n != 1 else ''})" if n else ""))
        log(f"  -> {d}")
        self._reply(200, {"ok": True, "saved": str(d)})


def main():
    # `global` must come before the name is used anywhere in the function --
    # including inside an argparse default. Declaring it lower down is a syntax
    # error, not a runtime one, so the whole file refuses to parse.
    global OUT

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("MOM_LISTENER_PORT", 8787)))
    ap.add_argument("--dir", default=str(OUT))
    ap.add_argument("--bind", default="0.0.0.0",
                    help="0.0.0.0 so the Pi can reach it; never port-forward this")
    a = ap.parse_args()

    OUT = Path(a.dir)

    if not SECRET:
        print("MOM_WEBHOOK_SECRET is not set.\n", file=sys.stderr)
        print("Refusing to start without it. An unauthenticated listener accepts", file=sys.stderr)
        print("anything on your network and looks like it is working.\n", file=sys.stderr)
        print("  PowerShell:  $env:MOM_WEBHOOK_SECRET='<same value as the Pi>'", file=sys.stderr)
        print("  Generate:    openssl rand -hex 32", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    log(f"Listening on {a.bind}:{a.port}")
    log(f"Writing tickets to {OUT.resolve()}")
    log("Set this on the Pi:")
    log(f"  MOM_WEBHOOK_URL=http://<this-pc-ip>:{a.port}/")
    log("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
