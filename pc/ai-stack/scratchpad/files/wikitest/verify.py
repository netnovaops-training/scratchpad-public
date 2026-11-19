#!/usr/bin/env python3
# Test helper: checks the test wiki at http://127.0.0.1:13000 against the seed.
# Usage: python verify.py SEED.json.gz ADMIN_PW_FILE
import gzip
import html
import json
import random
import re
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


BASE = "http://127.0.0.1:13000"
with gzip.open(sys.argv[1], "rt", encoding="utf-8") as fh:
    seed = json.load(fh)
pw = Path(sys.argv[2]).read_text(encoding="utf-8").strip()


def gql(query, variables=None, token=None):
    req = urllib.request.Request(BASE + "/graphql", method="POST",
                                 data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return json.load(urllib.request.urlopen(_http_only(req), timeout=60))  # nosec B310


# 1. Page count, as a guest (no sign-in at all)
guest_list = gql("{ pages { list { path locale title } } }")["data"]["pages"]["list"]
print(f"seed pages: {seed['pageCount']}   pages a guest can list: {len(guest_list)}")
seed_paths = {(p["locale"], p["path"]) for p in seed["pages"]}
new_paths = {(p["locale"], p["path"]) for p in guest_list}
print(f"same set of paths: {seed_paths == new_paths}")

# 2. Tags, as a guest
tags = gql("{ pages { tags { tag } } }")["data"]["pages"]["tags"]
print(f"seed tags: {len(seed['tags'])}   tags in new wiki: {len(tags)}   same: "
      f"{ {t['tag'] for t in seed['tags']} == {t['tag'] for t in tags} }")
by_tag = gql('{ pages { list(tags: ["genus"]) { path } } }')["data"]["pages"]["list"]
print(f"pages tagged 'genus': seed {sum('genus' in p['tags'] for p in seed['pages'])}, new {len(by_tag)}")

# 3. Five random pages fetched as a guest over plain HTTP
random.seed(20261013)
for p in random.sample(seed["pages"], 5):
    url = f"{BASE}/{p['locale']}/{p['path']}"
    body = urllib.request.urlopen(_http_only(url), timeout=60).read().decode("utf-8")  # nosec B310
    # a plain sentence from the page text (no markdown symbols), first 50 chars
    lines = [l.strip() for l in p["content"].splitlines()
             if len(l.strip()) > 30 and not re.search(r"[#*\[\]!|>_`]", l)]
    snippet = (lines[0] if lines else p["content"].strip().splitlines()[-1])[:50]
    found_title = html.escape(p["title"], quote=False) in body or p["title"] in body
    found_text = html.escape(snippet, quote=False) in body or snippet in body
    print(f"GET /{p['locale']}/{p['path']}: title found={found_title}, text found={found_text} "
          f"(snippet: {snippet!r})")

# 4. Signing in as the test admin
r = gql('mutation ($u: String!, $p: String!) { authentication { login(username: $u, password: $p, '
        'strategy: "local") { responseResult { succeeded message } jwt } } }',
        {"u": "robin-test@example.com", "p": pw})["data"]["authentication"]["login"]
print(f"admin sign-in succeeded={r['responseResult']['succeeded']} got-token={bool(r['jwt'])}")
cfg = gql("{ site { config { title description company host } } navigation { tree { locale items { label } } } }",
          token=r["jwt"])["data"]
print(f"site config: {cfg['site']['config']}")
print(f"menu items: {sum(len(t['items']) for t in cfg['navigation']['tree'])} "
      f"(seed {sum(len(t['items']) for t in seed['navigation']['tree'])})")

# 5. Wrong password must fail
r = gql('mutation { authentication { login(username: "robin-test@example.com", password: "wrong-password-123", '
        'strategy: "local") { responseResult { succeeded } } } }')
print(f"wrong password rejected: {not (r.get('data') or {}).get('authentication', {}).get('login', {}).get('responseResult', {}).get('succeeded')}")
