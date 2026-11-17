#!/usr/bin/env python3
"""findings.py - turn one lint or audit tool's output into the shared findings.json the NUC pulls (2026-11-17).

    python3 tools/ci/findings.py clippy      clippy.json  --root pc/ai-stack/rust --out findings-clippy.json
    python3 tools/ci/findings.py cargo-audit audit.json   --root pc/ai-stack/rust --out findings-cargo-audit.json
    python3 tools/ci/findings.py sarif       detekt.sarif --tool detekt          --out findings-detekt.json

Why: this repo is PRIVATE, so GitHub's Security tab (code scanning) will not take SARIF uploads, and a run
shows at most 10 warning + 10 error annotations per step. So every workflow here writes the same small JSON
list, uploads it as an artifact named findings-<tool>, and ~/learning-src/github_pull.py on the NUC
downloads it twice a day; todo_analysis.py then shows each finding on the to-do items that name its file.
Each row: {tool, rule, severity, path (repo-relative), line, message}. No code text, no secret values.
Also writes a short run summary ($GITHUB_STEP_SUMMARY) and the 10 worst findings as annotations.
Exit code is always 0: findings are hints, not a gate; a broken tool fails its own workflow step instead.
OUTCOME 2026-11-17: tested in a rust:1.91-bookworm container on the NUC with the same commands as rust.yml:
clippy gave 16 findings (the copy does not compile: module `share` and templates garden.html, theme_garden.html
are missing), cargo-audit 3 (RUSTSEC-2026-0387 crypto-bigint, RUSTSEC-2023-0071 rsa, basic-toml unmaintained).
The SARIF path (detekt, SwiftLint) is not run yet: the repo has no Kotlin or Swift.
"""
import argparse
import collections
import json
import os
import sys

SEV = {"error": 0, "critical": 0, "high": 1, "warning": 2, "medium": 2, "moderate": 2, "low": 3, "note": 4,
       "help": 4, "none": 5}


def clippy(path, root):
    """cargo clippy --message-format=json -> rows (one per primary span; duplicates across crates dropped)."""
    rows, seen = [], set()
    for line in open(path, encoding="utf-8"):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") != "compiler-message" or m["message"]["level"] not in ("warning", "error"):
            continue
        msg = m["message"]
        span = next((s for s in msg.get("spans", []) if s.get("is_primary")), None)
        if not span:
            continue
        p = os.path.normpath(os.path.join(root, span["file_name"])).replace("\\", "/")
        key = (p, span["line_start"], msg["message"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({"tool": "clippy", "rule": (msg.get("code") or {}).get("code") or "rustc",
                     "severity": msg["level"], "path": p, "line": span["line_start"], "message": msg["message"]})
    return rows


def cargo_audit(path, root):
    """cargo audit --json -> one row per vulnerable crate (warnings such as unmaintained count as 'low')."""
    d = json.load(open(path, encoding="utf-8"))
    lock = f"{root.rstrip('/')}/Cargo.lock"
    rows = []
    for v in d.get("vulnerabilities", {}).get("list", []):
        a, pk = v["advisory"], v["package"]
        fix = ", ".join(v.get("versions", {}).get("patched", [])) or "no fix yet"
        rows.append({"tool": "cargo-audit", "rule": a["id"], "severity": "high", "path": lock, "line": None,
                     "message": f"{pk['name']} {pk['version']}: {a['title']} (fixed in {fix})"})
    for kind, items in d.get("warnings", {}).items():
        for w in items:
            a, pk = w.get("advisory") or {}, w["package"]
            rows.append({"tool": "cargo-audit", "rule": a.get("id") or kind, "severity": "low", "path": lock,
                         "line": None, "message": f"{pk['name']} {pk['version']}: {kind} {a.get('title', '')}".strip()})
    return rows


def sarif(path, tool):
    """Any SARIF 2.1 file (detekt, swiftlint, ...) -> rows; absolute runner paths cut back to repo-relative."""
    d = json.load(open(path, encoding="utf-8"))
    ws = os.environ.get("GITHUB_WORKSPACE", os.getcwd()).replace("\\", "/").rstrip("/") + "/"
    rows = []
    for run in d.get("runs", []):
        for r in run.get("results", []):
            loc = (r.get("locations") or [{}])[0].get("physicalLocation", {})
            uri = loc.get("artifactLocation", {}).get("uri", "").replace("file://", "")
            rows.append({"tool": tool, "rule": r.get("ruleId"), "severity": r.get("level", "warning"),
                         "path": uri[len(ws):] if uri.startswith(ws) else uri.lstrip("./"),
                         "line": loc.get("region", {}).get("startLine"),
                         "message": " ".join(r.get("message", {}).get("text", "").split())[:300]})
    return rows


def report(rows, tool):
    """Run summary + the 10 worst as annotations (GitHub shows at most 10 per level per step anyway)."""
    by_sev = collections.Counter(r["severity"] for r in rows)
    by_rule = collections.Counter(r["rule"] for r in rows)
    text = [f"### {tool}", "", f"- findings: {len(rows)} ({', '.join(f'{k}: {v}' for k, v in by_sev.items()) or 'none'})",
            "", "Most common:", ""] + [f"- `{k}`: {v}" for k, v in by_rule.most_common(10)]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(text) + "\n")
    for r in sorted(rows, key=lambda r: SEV.get(str(r["severity"]).lower(), 5))[:10]:
        level = "error" if SEV.get(str(r["severity"]).lower(), 5) <= 1 else "warning"
        where = f"file={r['path']}" + (f",line={r['line']}" if r["line"] else "")
        print(f"::{level} {where},title={tool} {r['rule']}::{r['message'][:200]}")
    print(f"{tool}: {len(rows)} findings", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["clippy", "cargo-audit", "sarif"])
    ap.add_argument("file")
    ap.add_argument("--root", default=".")
    ap.add_argument("--tool")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not os.path.exists(a.file):
        rows = []  # the tool had nothing to scan (no Kotlin/Swift yet): an empty list, not a failure
    elif a.kind == "clippy":
        rows = clippy(a.file, a.root)
    elif a.kind == "cargo-audit":
        rows = cargo_audit(a.file, a.root)
    else:
        rows = sarif(a.file, a.tool or "sarif")
    tool = a.tool or a.kind
    json.dump(rows, open(a.out, "w", encoding="utf-8"), indent=1)
    report(rows, tool)


if __name__ == "__main__":
    main()
