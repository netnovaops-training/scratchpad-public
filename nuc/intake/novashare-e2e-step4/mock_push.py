"""mock_push.py - a local stand-in for a web push service (Novashare E2E Step 4 sandbox, 2026-11-24). Test only.

    python mock_push.py [PORT]           (default 8099; push_sbx.sh e2e runs it in its own container)

POST /p/<name>     stores the request (headers + body, base64) and answers 201; a name starting "gone" answers 410
                   (as a push service does for an expired subscription), "flaky" answers 503 the first time, then 201.
GET  /captured/<name>   the stored requests for <name>, as JSON (what test_push_e2e.py decrypts).
Nothing is written to disk; nothing is forwarded anywhere.
"""
import base64
import http.server
import json
import re
import sys
import threading

CAP: dict[str, list] = {}
SEEN: set[str] = set()
LOCK = threading.Lock()
NAME = re.compile(r"^/(p|captured)/([A-Za-z0-9_-]{1,80})$")


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        m = NAME.match(self.path)
        if not m or m.group(1) != "p":
            return self._send(404)
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(min(n, 8192))
        name = m.group(2)
        with LOCK:
            CAP.setdefault(name, []).append({"headers": dict(self.headers), "body": base64.b64encode(body).decode()})
            first = name not in SEEN
            SEEN.add(name)
        if name.startswith("gone"):
            return self._send(410)
        if name.startswith("flaky") and first:
            return self._send(503)
        return self._send(201)

    def do_GET(self):  # noqa: N802
        m = NAME.match(self.path)
        if not m or m.group(1) != "captured":
            return self._send(404)
        with LOCK:
            data = json.dumps(CAP.get(m.group(2), [])).encode()
        return self._send(200, data, "application/json")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    http.server.ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
