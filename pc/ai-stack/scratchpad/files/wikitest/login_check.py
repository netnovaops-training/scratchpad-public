import json
import sys
import urllib.request
from pathlib import Path


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


pw = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
q = {"query": 'mutation($u:String!,$p:String!){authentication{login(username:$u,password:$p,strategy:"local"){responseResult{succeeded}}}}',
     "variables": {"u": "robin-test@example.com", "p": pw}}
req = urllib.request.Request("http://127.0.0.1:13000/graphql", data=json.dumps(q).encode(),
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(_http_only(req), timeout=30))  # nosec B310
print("admin sign-in:", r["data"]["authentication"]["login"]["responseResult"]["succeeded"])
