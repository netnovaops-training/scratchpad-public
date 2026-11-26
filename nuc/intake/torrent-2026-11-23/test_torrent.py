"""test_torrent.py - proof for torrent.py against a FAKE qBittorrent (in this process). Run INSIDE the patched image:

    docker run --rm --entrypoint python IMAGE test_torrent.py

Checks: find_magnet in text; admin magnet -> login + category + add(urls) -> queued; wrong password -> error row;
non-admin magnet -> held file in PENDING_ROOT; admin .torrent -> add(torrents) + file consumed; a fake ".torrent"
that is not bencoded -> error, file kept; route_file sends .torrent to torrent.py (not unsorted).
"""
import http.server
import os
import pathlib
import sys
import tempfile
import threading
import urllib.parse

seen = []


class Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        seen.append((self.path, body, self.headers.get("Cookie", "")))
        if self.path == "/api/v2/auth/login":
            ok = b"password=right" in body
            self.send_response(200)
            if ok:
                self.send_header("Set-Cookie", "SID=abc; HttpOnly; path=/")
            self.end_headers()
            self.wfile.write(b"Ok." if ok else b"Fails.")
            return
        authed = "SID=abc" in (self.headers.get("Cookie") or "")
        self.send_response(200 if authed else 403)
        self.end_headers()
        self.wfile.write(b"Ok." if authed else b"Forbidden")

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
root = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
                   "GOTENBERG_URL": "http://x", "QBIT_URL": f"http://127.0.0.1:{srv.server_port}", "QBIT_USER": "jason",
                   "QBIT_PASS": "right", "PENDING_ROOT": str(root / "pending"), "INTAKE_UNSORTED": str(root / "u"),
                   "INTAKE_STAGE": str(root / "s"), "INTAKE_LOG": str(root / "log")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import torrent  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


MAG = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test"
check("find_magnet in text", torrent.find_magnet(f"look {MAG} here") == MAG and torrent.find_magnet("https://x.y/") is None)
app._person = lambda e: "jason"
row = torrent.handle_magnet(MAG, "j@x", [])
adds = [s for s in seen if s[0] == "/api/v2/torrents/add"]
check(f"admin magnet queued ({row['status']})", row["status"] == "queued" and adds and urllib.parse.quote(MAG, safe="").encode()[:30] in adds[-1][1].replace(b"%3A", b"%3A"))
check("category created", any(s[0] == "/api/v2/torrents/createCategory" for s in seen))
torrent.PASSWORD = "wrong"
d = []
row = torrent.handle_magnet(MAG, "j@x", d)
check("wrong password -> error row + detail", row["status"] == "error" and d)
torrent.PASSWORD = "right"
app._person = lambda e: "casey"
row = torrent.handle_magnet(MAG, "z@x", [])
check("non-admin magnet held", row["status"] == "held" and list((root / "pending" / "casey" / "torrent").glob("*.magnet")))
app._person = lambda e: "jason"
t = root / "a.torrent"
t.write_bytes(b"d8:announce3:urle")
before = len([s for s in seen if s[0] == "/api/v2/torrents/add"])
res = torrent.handle_file(t, "a.torrent", "j@x")
check("admin .torrent queued and consumed", res["status"] == "queued" and not t.exists()
      and len([s for s in seen if s[0] == "/api/v2/torrents/add"]) == before + 1)
bad = root / "b.torrent"
bad.write_bytes(b"<html>not a torrent")
res = torrent.handle_file(bad, "b.torrent", "j@x")
check("non-bencoded file refused, kept", res["status"] == "error" and bad.exists())
app.scan.ENABLED = False
t2 = root / "c.torrent"
t2.write_bytes(b"d4:infod4:name1:xee")
r = app.route_file(t2, "c.torrent", t2.stat().st_size, "j@x")
check(f"route_file sends .torrent to qBittorrent ({r.get('status')})", r.get("status") == "queued" and r.get("kind") == "torrent")
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
