#!/usr/bin/env python3
"""skills_prompt.py - pick review/design skills by number for ONE to-do scope, and get a copy-paste prompt.

Run on the NUC:
  python3 ~/learning-src/skills_prompt.py menu [tab]                  numbered skills; tabs: ui security backend code mcp
  python3 ~/learning-src/skills_prompt.py doctor "project:Sign-on"    run FIRST: which skills this scope still needs
  python3 ~/learning-src/skills_prompt.py T0944 14,6 before           prompt: skills 14 and 6, before coding
  python3 ~/learning-src/skills_prompt.py done T0944 14,6             record that they ran (skipped next time)
  python3 ~/learning-src/skills_prompt.py reset T0944 [14,6|all]      new scope: let doctor offer them again
  scopes: T0123 | section:NAME | project:NAME | computer:NAME | domain:TEXT
  stages: before (plan) | review (after the GitHub code-health results) | finish (prove it is done)
Reads ~/nuc-homelab/TODO-ALL.md; `done` and `reset` add one line to the item through todo_note.py (shared lock).
Every prompt starts with /caveman (Jason, 11-17: keep caveman on to save tokens).

Why: Jason, 11-16/11-17: never run every skill on everything; run Doctor at the start of each project so only the
skills a scope needs run, and reset a skill when the scope changes (a page already reviewed for security skips 6).
OUTCOME 2026-11-17: v2 adds the UI/UX tab (Impeccable, jakubkrehel, Vercel guideline skills, installed 11-17),
doctor, done, reset. Tested against the live list.
"""
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

LIST = Path.home() / "nuc-homelab/TODO-ALL.md"
NOTE = Path.home() / "learning-src/todo_note.py"

# number: (tab, invoke, what, best stages, cost, words that make it fit)
SKILLS = {
    1: ("ui", "/modern-web-guidance:modern-web-guidance", "current best practice for HTML, CSS, browser JS", "before, finish", "low", ("html", "css", "javascript", "js", "page")),
    2: ("ui", "/ui-ux-pro-max:ui-ux-pro-max", "full page design: layout, readability, mobile-first", "before", "medium", ("ui", "page", "design", "mobile", "wall")),
    3: ("ui", "/design:accessibility-review", "WCAG 2.1 AA audit", "finish", "low", ("ui", "page", "form")),
    13: ("ui", "/impeccable", "design workflow: set design context once (/impeccable init), then critique and polish", "before", "medium", ("ui", "page", "design", "look", "wording")),
    14: ("ui", "/better-interface", "ONE ranked report across accessibility, layout, writing, type, color, polish (cheapest whole-UI pass)", "review, finish", "low", ("ui", "page", "form", "wall", "cosmetic")),
    15: ("ui", "/better-accessibility", "keyboard, focus, ARIA, screen readers, motion, zoom (WCAG 2.2)", "finish", "low", ("ui", "form", "page", "accessib")),
    16: ("ui", "/better-writing", "interface copy: labels, errors, empty states, plain words", "finish", "low", ("wording", "page", "plain")),
    17: ("ui", "/make-interfaces-feel-better", "polish: radius, shadows, icons, motion, hover states", "finish", "low", ("cosmetic", "look", "polish", "animation")),
    18: ("ui", "/web-design-guidelines", "check UI code against Vercel's Web Interface Guidelines", "review", "low", ("ui", "html", "page")),
    19: ("ui", "/design:design-critique", "structured critique: usability, hierarchy, consistency (was /interface-review, not installed)", "finish", "medium", ("ui", "page")),
    20: ("ui", "/writing-guidelines", "docs and prose voice check", "finish", "low", ("docs", "guide", "manual", "readme")),
    4: ("backend", "/backend-design:design", "design a NEW server piece before writing it", "before", "low", ("automation", "unfinished", "new", "server", "cron")),
    5: ("backend", "/backend-design:audit", "audit an EXISTING backend component", "review, finish", "medium", ("code", "broken", "api", "service", "backup")),
    6: ("security", "/soundcheck:security-review", "security review of the files in scope", "review, finish", "medium", ("security", "auth", "sign", "secret", "key", "caddy")),
    8: ("security", "/claude-security:claude-security", "deep multi-agent scan of a WHOLE repo plus patches (heavy; final runs only)", "final", "high", ()),
    10: ("security", "/fix-github-alerts", "pull GitHub code-health and Dependabot findings into fixes", "review", "medium", ("security", "dependabot")),
    12: ("security", "/docker-image-scan:scan", "scan the container images in scope", "review, finish", "low", ("docker", "container", "compose", "image")),
    7: ("code", "/code-review low", "correctness review of the diff", "finish", "low", ("code", "broken", "bug")),
    11: ("code", "/repo-hygiene:gating-commits", "before committing: tests, docstrings, docs", "finish", "low", ("code", "docs")),
    9: ("mcp", "/anthropic-skills:mcp-builder", "build or check an MCP server", "before, finish", "low", ("mcp",)),
}
STAGE = {
    "before": "BEFORE coding: plan it. Read each item's Where/Found/Do/Review/Answer lines and its todo-code file. "
              "Report a short plan per item; write nothing to the code.",
    "review": "AFTER the GitHub code review: pull the latest code-health run for the repo holding these files "
              "(gh run list -R netnovaops-training/<repo> --limit 3; gh run view <id> --log). Keep only findings "
              "in the files below.",
    "finish": "AFTER finishing: prove it is done. Run each skill on the finished files, test the Do line's "
              "'finished when', then tick the item (Done: line).",
}


def items():
    comp = proj = sec = None
    cur = None
    for line in LIST.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            comp = line[3:].strip()
        elif line.startswith("### "):
            proj = line[4:].strip()
        elif line.startswith("#### "):
            sec = line[5:].strip()
        m = re.match(r"^- \[( |x)\] \*\*(T\d+)\*\* (.*)", line)
        if m:
            if cur:
                yield cur
            cur = {"done": m.group(1) == "x", "id": m.group(2), "head": m.group(3), "comp": comp, "proj": proj,
                   "sec": sec, "text": line}
        elif cur and line.startswith("  "):
            cur["text"] += "\n" + line
        elif cur and line.strip():
            yield cur
            cur = None
    if cur:
        yield cur


def pick(scope):
    allx = [i for i in items() if not i["done"]]
    if re.fullmatch(r"T\d+", scope):
        return [i for i in allx if i["id"] == scope], scope
    kind, _, val = scope.partition(":")
    v = val.strip().lower()
    match = {"section": lambda i: (i["sec"] or "").lower() == v, "project": lambda i: (i["proj"] or "").lower() == v,
             "computer": lambda i: (i["comp"] or "").lower() == v, "domain": lambda i: v in i["text"].lower()}
    if kind not in match:
        sys.exit("scope must be T0123, section:NAME, project:NAME, computer:NAME or domain:TEXT")
    return [i for i in allx if match[kind](i)], f"{kind} '{val.strip()}'"


def ran(item):
    """Skill numbers already run on this item since its last reset (from Skills-done / Skills-reset lines)."""
    done = set()
    for line in item["text"].split("\n"):
        m = re.match(r"\s+Skills-(done|reset): ([\d,]+|all)", line)
        if not m:
            continue
        nums = set(SKILLS) if m.group(2) == "all" else {int(x) for x in m.group(2).split(",")}
        done = done | nums if m.group(1) == "done" else done - nums
    return done


def fits(item):
    """Skills whose words fit the item. Reads ONLY the title, its [tags] and the Where/Do lines.
    OUTCOME 2026-11-17 (T1017): it used to read the whole item, so a Review line saying "page" gave the
    script-recovery item T1012 eight UI skills. Review/Ask/Answer text is commentary, not scope."""
    tags = set(re.findall(r"\[([a-z-]+)\]", item["head"].lower()))
    scope = [item["head"]] + [l.strip()[l.strip().index(":") + 1:] for l in item["text"].split("\n")[1:]
                              if l.strip().startswith(("Where:", "Do:"))]
    words = "\n".join(scope).lower()
    out = {}
    for n, (_, _, _, _, cost, keys) in SKILLS.items():
        # short keys need a whole word: "js" matched "jsonl" (T1017); "copy" dropped (a backup copy is not UI copy)
        why = [k for k in keys if k in tags or re.search(rf"\b{re.escape(k)}" + (r"\b" if len(k) <= 4 else ""), words)]
        if why:
            out[n] = why
    return out


def doctor(scope):
    chosen, label = pick(scope)
    if not chosen:
        sys.exit(f"no open items in {label}")
    count, skipped = {}, {}
    for i in chosen:
        done = ran(i)
        for n in fits(i):
            if n in done:
                skipped[n] = skipped.get(n, 0) + 1
            else:
                count[n] = count.get(n, 0) + 1
    cost = {"low": 0, "medium": 1, "high": 2}
    order = sorted(count, key=lambda n: (cost[SKILLS[n][4]], -count[n]))
    print(f"Doctor for {label}: {len(chosen)} open items")
    tabs = {SKILLS[n][0] for n in order}
    for tab in ("ui", "security", "backend", "code", "mcp"):
        print(f"  {tab:9} {'needed' if tab in tabs else 'not needed for this scope (skip, saves tokens)'}")
    print("\nRecommended, cheapest first (items it fits / already done):")
    for n in order:
        print(f"  {n:>3}. {SKILLS[n][1]:42} {SKILLS[n][4]:6} fits {count[n]}, done on {skipped.get(n, 0)}")
    lean = [n for n in order if SKILLS[n][4] == "low"][:3]
    if lean:
        print(f"\nLean start: python3 ~/learning-src/skills_prompt.py \"{scope}\" {','.join(map(str, lean))} before")
    if skipped and not count:
        print("Everything that fits already ran. New scope? reset first: skills_prompt.py reset <T> all")


def note(item_id, line):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({item_id: [line]}, f)
    subprocess.run([sys.executable, str(NOTE), f.name], check=True)


def main():
    a = sys.argv[1:]
    if not a or a[0] == "menu":
        tab = a[1] if len(a) > 1 else None
        for t in ("ui", "security", "backend", "code", "mcp"):
            if tab and t != tab:
                continue
            print(f"[{t.upper()}]")
            for n, (tt, inv, what, stage, cost, _) in sorted(SKILLS.items()):
                if tt == t:
                    print(f"{n:>4}. {inv} - {what}\n      best: {stage}; cost: {cost}")
        return
    if a[0] == "doctor":
        return doctor(a[1])
    if a[0] in ("suggest",):
        return doctor(a[1])
    if a[0] in ("done", "reset"):
        nums = a[2] if len(a) > 2 else "all"
        note(a[1], f"Skills-{a[0]}: {nums} ({date.today()})")
        return
    if len(a) < 3 or a[2] not in STAGE:
        sys.exit(__doc__)
    chosen, label = pick(a[0])
    nums = [int(x) for x in a[1].split(",")]
    if not chosen:
        sys.exit(f"no open items in {label}")
    ids = ", ".join(i["id"] for i in chosen[:60]) + (" ..." if len(chosen) > 60 else "")
    lines = ["/caveman", "", f"Scope: {label} - {len(chosen)} open to-do items: {ids}.",
             ("Read those items in ~/nuc-homelab/TODO-ALL.md on the NUC first (Where/Found/Do/Review/Answer lines) "
             "and their todo-code/ files. Stay inside this scope."), "", "Stage: " + STAGE[a[2]], "",
             "Run these skills one at a time, in this order, on the files below only:"]
    for k, n in enumerate(nums, 1):
        lines.append(f"  {k}. {SKILLS[n][1]} - {SKILLS[n][2]} (cost: {SKILLS[n][4]})")
    lines += ["", "Files in scope (Where and Code lines):"]
    for i in chosen[:40]:
        for ln in i["text"].split("\n"):
            if ln.strip().startswith(("Where:", "Code:")):
                lines.append(f"  {i['id']}: {ln.strip()}")
    lines += ["", ("Rules: guide, don't solve - each finding becomes a Review: line on its item (todo_note.py). Fix only "
              "what I ask. Tell me before any skill marked high. After each skill: two-line summary, then record it: "
              "python3 ~/learning-src/skills_prompt.py done <T-number> <skill number>.")]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
