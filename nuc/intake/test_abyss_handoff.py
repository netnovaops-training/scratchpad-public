"""test_abyss_handoff.py - Abyss step 5 on the Novashare side (2026-11-24): the Abyss gateway row + health check, and the
"Send as Abyss" button on /sending (Jason only, strict origin + window checks, transferable buffers)."""
import asyncio, json, os, pathlib, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
for k, v in {"INTAKE_KEY": "k", "INTAKE_TOKEN": "t", "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
             "GOTENBERG_URL": "http://127.0.0.1:9"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app, atomic_ui, gateway, portal
fails = 0
def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label); fails += not ok
app._person = lambda e: "jason" if (e or "").startswith("j") else "casey"

# --- gateway row
rows = gateway.routes()
ab = [r for r in rows if r["name"] == "Abyss"]
check("one Abyss row: NUC, container abyss, Jason only, health check 'abyss'", len(ab) == 1 and ab[0]["computer"] == "NUC"
      and ab[0]["container"] == "abyss" and ab[0]["grant"] == "Jason only" and ab[0]["check"] == "abyss")
check("Abyss row comes before the classifier rows and 'Anything else'",
      [r["name"] for r in rows].index("Abyss") < [r["name"] for r in rows].index("Anything else"))

codes = {"code": 200}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(codes["code"] if self.path == "/healthz" else 404); self.end_headers()
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["ABYSS_HEALTH_URL"] = "http://127.0.0.1:%d" % srv.server_port
check("health: 200 from /healthz -> green", asyncio.run(gateway.check("abyss")) == "green")
codes["code"] = 403
check("health: 403 (peer gate) -> red, not green", asyncio.run(gateway.check("abyss")) == "red")
os.environ["ABYSS_HEALTH_URL"] = "http://127.0.0.1:9"
check("health: nothing listening -> red", asyncio.run(gateway.check("abyss")) == "red")
data = json.loads(asyncio.run(gateway.routes_json("j@example.invalid")).body)
check("routes.json lists Abyss with a health value", any(r["name"] == "Abyss" and r["health"] in ("green", "red", "grey")
                                                      for r in data["routes"]))
page = asyncio.run(gateway.routes_page("j@example.invalid")).body
check("routes page shows the Abyss row", b"Abyss" in page and b"one-time view-only link" in page)

# --- /sending button
j = atomic_ui.sending("j@example.invalid").body.decode()
z = atomic_ui.sending("z@example.invalid").body.decode()
n = atomic_ui.sending(None).body.decode()
check("Jason's /sending has the hidden Send as Abyss button with the Abyss origin",
      '<button id=abyss hidden data-to="https://abyss.home.example.net">Send as Abyss</button>' in j)
check("others (and no identity) get no button and no placeholder", "id=abyss " not in z and "id=abyss " not in n
      and "ABYSS-BUTTON" not in z + n + j)
check("handoff JS: exact-origin AND opened-window checks before anything is sent",
      "e.origin !== to || e.source !== w" in j and "abyss-ready" in j)
check("handoff JS: bytes posted to that origin only, as transferable buffers",
      "w.postMessage({type: 'abyss-files', files}, to, files.map(f => f.buffer))" in j and "'*'" not in j)
check("only photos and videos are kept for Abyss, 20 at most", "/^(image|video)\\//.test(t) && media.length < 20" in j)
check("the button shows only after the share is done", "showAbyss();" in j and j.index("showAbyss();") > j.index("$('title').textContent = anyBad"))
check("no email in the page", "@example.invalid" not in j)
check("Abyss link in Jason's admin links only", 'abyss.home.example.net/new' in portal.page("j@x", "jason")
      and 'abyss.home.example.net/new' not in portal.page("z@x", "casey"))
srv.shutdown()
print("ALL PASS" if not fails else f"{fails} FAILED"); sys.exit(1 if fails else 0)
