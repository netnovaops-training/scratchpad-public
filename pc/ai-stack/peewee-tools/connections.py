#!/usr/bin/env python3
# =============================================================================
#  connections.py -- render the spoken link lines (2026-09-01).
#
#  data/connections.json holds KNOWN links between people on the dial (the
#  house rule lives in that file: no entry, no mention, nothing invented).
#  This renders any entry that has text but no audio yet, in Randy's voice,
#  and writes the audio path back into the JSON. Idempotent.
# =============================================================================
import json
import os
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


VOICE_URL = (os.environ.get("VOICE_NARRATE_URL")
             or os.environ.get("VOICE_URL",
                               "http://localhost:8095/v1/audio/speech"))
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))


def say(text, out):
    body = json.dumps({"input": text, "voice": "Randy",
                       "response_format": "mp3", "normalize": True,
                       "exaggeration": 0.55, "cfg_weight": 0.5}).encode()
    req = urllib.request.Request(VOICE_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
        data = r.read()
    if len(data) < 2000:
        raise RuntimeError("suspiciously small audio")
    with open(out, "wb") as f:
        f.write(data)


def main():
    os.chdir(SITE)
    os.makedirs(os.path.join("data", "connections"), exist_ok=True)
    p = os.path.join("data", "connections.json")
    doc = json.loads(Path(p).read_text(encoding="utf-8"))
    changed = 0
    for link in doc.get("links", []):
        if link.get("audio") or not link.get("text"):
            continue
        out = os.path.join("data", "connections",
                           f"{link['a']}--{link['b']}.mp3")
        print("rendering", out, "--", link["text"][:60], flush=True)
        say(link["text"], out)
        link["audio"] = out.replace(os.sep, "/")
        changed += 1
    if changed:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
    print(f"rendered {changed} link lines")


if __name__ == "__main__":
    main()
