#!/usr/bin/env python3
"""gotenberg_web_patch.py - add a separate Gotenberg for printing shared links to PDF (T0872, 2026-11-18).

Run in ~/nuc-homelab. Adds service gotenberg-web on a new network `webprint` that only intake joins.
Why a second instance: the existing `gotenberg` is Paperless's and is locked to file:///tmp/ with JavaScript
off on purpose (an open URL renderer beside the archive is an SSRF engine). Shared links (Gemini pages)
need JavaScript and the public web, so they get their own renderer that:
  - allows only http(s) URLs and DENIES private, loopback, link-local, LAN, Tailscale (100.64/10) and
    single-label hosts (container names) via Gotenberg's own allow/deny lists, which its pinning proxy
    applies to every sub-request too (seen in its log: "CONNECT blocked ... does not match");
  - sits on `webprint` only, so no other container can reach it.
intake's share.py also refuses non-public URLs before asking. Refuses to run twice.
"""
from pathlib import Path

f = Path("docker-compose.yml")
t = f.read_text(encoding="utf-8")
if "gotenberg-web:" in t:
    raise SystemExit("already patched")

DENY = (r"^https?://(localhost|[^/.:]+(:[0-9]+)?(/|$)|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\."
        r"|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|169\.254\.|0\.|\[).*")
service = f"""  gotenberg-web:
    # Shared links -> PDF for the Drop share sheet (T0872, 2026-11-18). NOT Paperless's gotenberg: that one
    # stays locked to file:///tmp/ with JavaScript off. This one renders public pages only (deny list =
    # private/LAN/Tailscale/loopback/container names) and is reachable only from intake, on `webprint`.
    image: gotenberg/gotenberg:8
    container_name: gotenberg-web
    restart: unless-stopped
    command:
      - "gotenberg"
      - "--chromium-allow-list=^https?://.*"
      - '--chromium-deny-list={DENY}'  # single quotes: YAML double quotes reject .
      - "--api-timeout=120s"
    networks: [webprint]
    mem_limit: 1g
    logging:
      driver: json-file
      options: {{ max-size: "10m", max-file: "3" }}

"""
anchor = "  tika:\n"
assert t.count(anchor) == 1
t = t.replace(anchor, service + anchor)

# intake joins webprint (and keeps the default network for the LLM, auth and the volumes it already uses)
old = "    build: ./intake\n    container_name: intake\n"
assert t.count(old) == 1
t = t.replace(old, old + "    # webprint: the only way to gotenberg-web (shared links -> PDF, T0872)\n    networks: [default, webprint]\n")

old_net = "  ci_scan:\n    name: ci_scan\n"
assert t.count(old_net) == 1
t = t.replace(old_net, "  webprint:\n    name: webprint\n" + old_net)
f.write_text(t, encoding="utf-8")
print("compose patched")
