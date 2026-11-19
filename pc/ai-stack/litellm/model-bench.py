#!/usr/bin/env python3
"""Can gemma4:12b replace five models on this card?

Runs INSIDE the ai-stack docker network, because on the host
localhost:11434 is shadowed by a second, empty Ollama (a native Windows
install listening on the IPv6 wildcard). The container's Ollama is the
real one and is reachable by name.

The tests are the stack's own routes, not a public benchmark:
  instr   -- follow a precise format. Agents die here, not on cleverness.
  reason  -- multi-step arithmetic whose naive answer is wrong.
  tools   -- EMIT a tool call, not describe one in prose. Small models
             fail this silently and it looks like the agent is broken.
  vision  -- read an actual photo from Jason's library.
"""
import base64
import json
import os
import sys
import time
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


BASE = os.environ.get("OLLAMA_BASE", "http://ollama:11434")
MODELS = os.environ.get("BENCH_MODELS", "gemma4:12b,qwen3:8b,qwen3-vl:8b").split(",")


def post(path, payload, timeout=600):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(_http_only(req), timeout=timeout) as r:  # nosec B310
        return json.loads(r.read())


def strip_think(t):
    return t.split("</think>", 1)[1] if "</think>" in t else t


def t_instr(m):
    r = post("/api/chat", {"model": m, "stream": False, "options": {"temperature": 0},
        "messages": [{"role": "user", "content":
            "Reply with exactly three words, lowercase, no punctuation, "
            "naming three primary colours."}]})
    txt = strip_think(r["message"]["content"]).strip()
    ok = (len(txt.split()) == 3 and txt == txt.lower()
          and not any(c in txt for c in ".,!?;:"))
    return ok, txt[:46]


def t_reason(m):
    r = post("/api/chat", {"model": m, "stream": False, "options": {"temperature": 0},
        "messages": [{"role": "user", "content":
            "A drive holds 103,180 files totalling 371 GB. 78 of them fail to "
            "copy. If the failures average 4.2 GB each, how many GB copied "
            "successfully? Answer with the number only."}]})
    txt = strip_think(r["message"]["content"])
    return ("43.4" in txt), txt.strip().replace("\n", " ")[:46]


def t_tools(m):
    r = post("/api/chat", {"model": m, "stream": False, "options": {"temperature": 0},
        "messages": [{"role": "user", "content":
            "What is the under-voltage state of the host named 'Tor'? Use the tool."}],
        "tools": [{"type": "function", "function": {
            "name": "get_power_state",
            "description": "Read a Raspberry Pi's throttling bitmask",
            "parameters": {"type": "object",
                           "properties": {"hostname": {"type": "string"}},
                           "required": ["hostname"]}}}]})
    calls = r["message"].get("tool_calls") or []
    if not calls:
        return False, "no tool_calls emitted"
    fn = calls[0].get("function", {})
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            args = {}
    ok = fn.get("name") == "get_power_state" and "tor" in str(
        args.get("hostname", "")).lower()
    return ok, f"{fn.get('name')}({args})"[:46]


def t_vision(m, img):
    if not img:
        return None, "-"
    try:
        r = post("/api/chat", {"model": m, "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "user", "images": [img],
                          "content": "Describe this image in one short sentence."}]})
        txt = strip_think(r["message"]["content"]).strip().replace("\n", " ")
        return (len(txt) > 15), txt[:46]
    except Exception as e:  # noqa: BLE001  benchmark: any model error scores as a fail
        return False, f"error: {str(e)[:38]}"


def main():
    img = ""
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        img = base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()

    print(f"{'MODEL':<15}{'INSTR':<7}{'REASON':<8}{'TOOLS':<7}{'VISION':<8}{'SECS':<7}")
    print("-" * 60)
    for m in MODELS:
        m = m.strip()
        t0 = time.time()
        try:
            oi, di = t_instr(m)
        except Exception as e:  # noqa: BLE001  benchmark: a model that errors is skipped
            print(f"{m:<15}SKIP  {str(e)[:40]}")
            continue
        orr, dr = t_reason(m)
        ot, dt = t_tools(m)
        ov, dv = t_vision(m, img)
        el = time.time() - t0

        def mk(v):
            return "PASS" if v else ("--" if v is None else "FAIL")
        print(f"{m:<15}{mk(oi):<7}{mk(orr):<8}{mk(ot):<7}{mk(ov):<8}{el:<7.0f}")
        for lbl, d in (("instr", di), ("reason", dr), ("tools", dt), ("vision", dv)):
            print(f"   {lbl:<7}{d}")
        print()


if __name__ == "__main__":
    main()
