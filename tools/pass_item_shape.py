"""Print one Pass item's JSON SHAPE: keys, types, and string LENGTHS only.

2026-11-24: used to learn which field holds a token without seeing it.
Every string value is replaced by <str len=N>; nothing else is printed.
    python -I pass_item_shape.py "NUC services" "JELLYFIN_API_KEY (2026-11-24)"
"""
import json, os, subprocess, sys

def shape(x):
    if isinstance(x, dict):
        # field NAMES ("name" keys, e.g. "API Key") are labels, not secrets
        return {k: (v if k == "name" and isinstance(v, str) else shape(v))
                for k, v in x.items()}
    if isinstance(x, list):
        return [shape(v) for v in x]
    if isinstance(x, str):
        return f"<str len={len(x)}>"
    return x

env = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")
p = subprocess.run(["pass-cli", "item", "view", "--vault-name", sys.argv[1],
                    "--item-title", sys.argv[2], "--output", "json"],
                   capture_output=True, text=True, env=env)
if p.returncode:
    print("failed:", (p.stderr or "").strip().splitlines()[:1]); sys.exit(1)
print(json.dumps(shape(json.loads(p.stdout)), indent=1))
