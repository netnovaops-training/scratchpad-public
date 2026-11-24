"""Find Proton Pass items by title match and report found / not found only.

Written 2026-11-24 for the ABS-admin and NUC-wall token checks. Prints the
vault, the matched title, and (with --field) the LENGTH of that one field.
Never prints a field value: values stay inside this process.

    python pass_find_items.py --vault "NUC services" --pattern paperless --pattern jellyfin --field password
    python pass_find_items.py --all-vaults --pattern audiobookshelf

--all-vaults skips Cards and IDs, Crypto and Wifi (pass-cli-safety rule).
Works with the PC's pass-cli (PROTON_PASS_KEY_PROVIDER=fs is set here).
"""
import argparse
import json
import os
import subprocess
import sys

SKIP = {"Cards and IDs", "Crypto", "Wifi"}
ENV = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")


def _run(args):
    """Run pass-cli and return stdout; stderr is reduced to its first line."""
    p = subprocess.run(["pass-cli", *args], capture_output=True, text=True, env=ENV)
    if p.returncode != 0:
        first = (p.stderr or "").strip().splitlines()[:1]
        raise RuntimeError(f"pass-cli {args[0]} {args[1]} failed: {first}")
    return p.stdout


def vaults():
    """All vault names except the never-list ones."""
    data = json.loads(_run(["vault", "list", "--output", "json"]))
    items = data.get("vaults", data) if isinstance(data, dict) else data
    return [v.get("name") for v in items if v.get("name") not in SKIP]


def titles(vault):
    """Item titles in one vault (titles only; no --show-secrets)."""
    data = json.loads(_run(["item", "list", "--vault-name", vault, "--output", "json"]))
    items = data.get("items", data) if isinstance(data, dict) else data
    out = []
    for it in items:
        t = it.get("title") or (it.get("content") or {}).get("title")
        if t:
            out.append(t)
    return out


def field_len(vault, title, field):
    """Length of one field's value; the value itself is discarded."""
    try:
        v = _run(["item", "view", "--vault-name", vault, "--item-title", title,
                  "--field", field]).strip()
    except RuntimeError:
        return None
    return len(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", action="append", default=[])
    ap.add_argument("--all-vaults", action="store_true")
    ap.add_argument("--pattern", action="append", required=True)
    ap.add_argument("--field", default=None)
    a = ap.parse_args()
    vs = vaults() if a.all_vaults else a.vault
    hits = {p: [] for p in a.pattern}
    for v in vs:
        try:
            ts = titles(v)
        except RuntimeError as e:
            print(f"[{v}] {e}")
            continue
        for p in a.pattern:
            for t in ts:
                if p.lower().replace("_", " ") in t.lower().replace("_", " "):
                    hits[p].append((v, t))
    for p, found in hits.items():
        if not found:
            print(f"{p}: not found")
        for v, t in found:
            extra = ""
            if a.field:
                n = field_len(v, t, a.field)
                extra = f" field '{a.field}' length={n}" if n is not None else f" field '{a.field}' missing"
            print(f"{p}: found in '{v}' as '{t}'{extra}")


if __name__ == "__main__":
    sys.exit(main())
