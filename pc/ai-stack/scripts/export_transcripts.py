#!/usr/bin/env python3
"""
Export transcripts.

Every conversation across every surface is already plain JSONL under
./transcripts/. This turns them into something you can read, archive, or feed
somewhere else.

    python scripts/export_transcripts.py --list
    python scripts/export_transcripts.py --since 2026-08-01 --format md
    python scripts/export_transcripts.py --surface telegram --format json --merge
    python scripts/export_transcripts.py --session a1b2c3d4 --format txt

Runs on the host with plain Python 3 - no dependencies, no container needed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "transcripts"
EXPORTS = TRANSCRIPTS / "exports"


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def discover(surface: str | None, since: str | None, session: str | None) -> list[Path]:
    if not TRANSCRIPTS.exists():
        return []
    files = sorted(TRANSCRIPTS.rglob("*.jsonl"))
    out = []
    for f in files:
        if "exports" in f.parts:
            continue
        parts = f.relative_to(TRANSCRIPTS).parts
        if len(parts) < 3:
            continue
        f_surface, f_day = parts[0], parts[1]
        if surface and f_surface != surface:
            continue
        if since and f_day < since:
            continue
        if session and session not in f.stem:
            continue
        out.append(f)
    return out


def to_markdown(rows: list[dict], title: str) -> str:
    lines = [f"# {title}", ""]
    for r in rows:
        if r.get("event"):
            lines += [f"> `{r['ts']}` — {r['event']}", ""]
            continue
        role = r.get("role", "?")
        who = {"user": "**You**", "assistant": "**Assistant**", "tool": "**Tool**"}.get(
            role, f"**{role}**"
        )
        route = r.get("job") or r.get("model")
        tag = f" · `{route}`" if route else ""
        if role == "tool":
            tool_name = (r.get("meta") or {}).get("tool", "")
            tag = f" · `{tool_name}`" if tool_name else tag
        lines += [f"{who}{tag}  <sub>{r['ts']}</sub>", ""]
        content = r.get("content", "")
        if role == "tool":
            lines += ["```", content[:4000], "```", ""]
        else:
            lines += [content, ""]
    return "\n".join(lines)


def to_text(rows: list[dict]) -> str:
    out = []
    for r in rows:
        if not r.get("content"):
            continue
        out.append(f"[{r['ts']}] {r.get('role', '?').upper()}: {r['content']}")
    return "\n\n".join(out)


def to_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "surface", "session", "role", "route", "content"])
        for r in rows:
            w.writerow(
                [
                    r.get("ts", ""),
                    r.get("surface", ""),
                    r.get("session", ""),
                    r.get("role", ""),
                    r.get("job") or r.get("model") or "",
                    (r.get("content") or "").replace("\n", "\\n"),
                ]
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Export ai-stack transcripts")
    ap.add_argument("--surface", choices=["hermes", "telegram", "webui"], help="filter by surface")
    ap.add_argument("--since", help="YYYY-MM-DD, inclusive")
    ap.add_argument("--session", help="session id (or a prefix of it)")
    ap.add_argument("--format", default="md", choices=["md", "txt", "json", "csv"])
    ap.add_argument("--merge", action="store_true", help="one combined file instead of one per session")
    ap.add_argument("--list", action="store_true", help="just show what's there")
    args = ap.parse_args()

    files = discover(args.surface, args.since, args.session)
    if not files:
        print("no transcripts matched.", file=sys.stderr)
        print(f"looked in: {TRANSCRIPTS}", file=sys.stderr)
        return 1

    if args.list:
        print(f"{len(files)} session(s):\n")
        for f in files:
            rows = load(f)
            turns = sum(1 for r in rows if r.get("content"))
            first = next((r["content"] for r in rows if r.get("role") == "user" and r.get("content")), "")
            print(f"  {f.relative_to(TRANSCRIPTS)}")
            print(f"    {turns} turns · {first[:70]}")
        return 0

    EXPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.merge:
        rows: list[dict] = []
        for f in files:
            rows.extend(load(f))
        rows.sort(key=lambda r: r.get("ts", ""))
        name = f"merged-{args.surface or 'all'}-{stamp}"
        if args.format == "json":
            p = EXPORTS / f"{name}.json"
            p.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        elif args.format == "csv":
            p = EXPORTS / f"{name}.csv"
            to_csv(rows, p)
        elif args.format == "txt":
            p = EXPORTS / f"{name}.txt"
            p.write_text(to_text(rows), encoding="utf-8")
        else:
            p = EXPORTS / f"{name}.md"
            p.write_text(to_markdown(rows, f"Transcripts — {args.surface or 'all surfaces'}"), encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}  ({len(rows)} entries from {len(files)} sessions)")
        return 0

    for f in files:
        rows = load(f)
        parts = f.relative_to(TRANSCRIPTS).parts
        base = f"{parts[0]}-{parts[1]}-{f.stem}"
        if args.format == "json":
            p = EXPORTS / f"{base}.json"
            p.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        elif args.format == "csv":
            p = EXPORTS / f"{base}.csv"
            to_csv(rows, p)
        elif args.format == "txt":
            p = EXPORTS / f"{base}.txt"
            p.write_text(to_text(rows), encoding="utf-8")
        else:
            p = EXPORTS / f"{base}.md"
            p.write_text(to_markdown(rows, base), encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
