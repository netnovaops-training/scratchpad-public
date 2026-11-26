#!/usr/bin/env python3
"""todo_next.py (T1017): print the ONE prompt that moves a to-do item to its next part.

Run on the NUC:
  python3 ~/learning-src/todo_next.py T1012        next step for one item
  python3 ~/learning-src/todo_next.py first        the top open [first] item (then P1) that is not waiting
  python3 ~/learning-src/todo_next.py questions    every unanswered Ask line of the [first] items, one list
  add --json for the /todo page (T1017 step 3) or the 14:00/18:00 review task.

How it decides (read-only; it never writes the list):
  stage   plan    no "slice/step/phase N done" Review line yet and no Skills-done line: plan + audit first
          build   a slice is done: the next numbered Do step is the next slice
          finish  every numbered Do step is done: re-audit, before/after table, tick, move to TODO-DONE.md
  skills  skills_prompt.py's doctor (fixed 11-17 to read only title/tags/Where/Do), filtered to the stage
          (plan = "before"+"review", build = none, finish = "review"/"finish"), not yet run, cheapest first, max 3
  asks    Ask lines with no Answer line under them, all in one numbered list. Jason's rule (11-17): ask only
          when a step cannot go on without the answer, all at once; if he says "defaults", use the
          recommended choice and write it as the Answer line.
Progress is read from Review lines like "(2026-11-17 eve, slice 1 done)". Write that phrase when a slice ends.
OUTCOME: (2026-11-17) written for T1017 step 2; tested on T1012 (build, slice 2), T1014 (phase 2), T0944.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skills_prompt as sp

DONE_RE = re.compile(r"\b(?:slice|step|phase|part)\s+(\d+)\s+(?:is\s+)?(?:done|finished)", re.IGNORECASE)
STEP_RE = re.compile(r"(?:^|\s)(\d+)\)\s+(.*?)(?=\s+\d+\)\s|$)")
STAGE_SKILLS = {"plan": ("before", "review"), "build": (), "finish": ("review", "finish")}
RULE = ("Rules: read HANDOFF.md first. One slice, about 1-2 hours, in 15-minute spurts; after each spurt add a "
        "3-line Review line (done / counts / errors). Back up before changing anything; copy, test, swap. "
        "Never print secrets. Ask only when the next step cannot go on without the answer, and then ask all "
        "open questions at once. When the slice ends, write a Review line containing \"slice N done\".")


def lines_of(item, key):
    """Field lines of one kind (e.g. "Ask:") from an item, text after the colon."""
    return [l.strip()[len(key):].strip() for l in item["text"].split("\n")[1:] if l.strip().startswith(key)]


def open_asks(item):
    """Ask lines that have no Answer line right under them."""
    body = [l.strip() for l in item["text"].split("\n")[1:]]
    return [l[4:].strip() for k, l in enumerate(body)
            if l.startswith("Ask:") and not (k + 1 < len(body) and body[k + 1].startswith("Answer:"))]


def steps(item):
    """Numbered steps from the Do line(s): {1: 'text', 2: 'text', ...}.

    Falls back to the item's 'Steps: a | b | c' line (todo_steps.py, T0069) when the Do line has no
    numbered '1) ... 2) ...' steps.
    """
    do = " ".join(lines_of(item, "Do:"))
    st = {int(n): t.strip() for n, t in STEP_RE.findall(do)}
    # OUTCOME 2026-11-22 autopilot: T0069's Do line is a 'Hint:' with no numbered steps, so st was {}
    # and plan_for() could never reach 'finish': after its Review said 'step 5 done' (all 5 Steps
    # ticked, 'Step: done 5/5') first --json still said 'build step 6', a step that does not exist.
    # Numbered Do steps still win; the Steps line is used only when there are none.
    if not st:
        plan = " ".join(lines_of(item, "Steps:"))
        st = {k: t.strip() for k, t in enumerate(plan.split(" | "), 1) if t.strip()}
    return st


def plan_for(item):
    """Stage, next step number and text, skills to run, open questions for one item."""
    reviews = lines_of(item, "Review:")
    done_n = max([int(n) for r in reviews for n in DONE_RE.findall(r[:80])] or [0])
    st = steps(item)
    started = done_n > 0 or any(l.strip().startswith("Skills-done:") for l in item["text"].split("\n"))
    if not started:
        stage, nxt = "plan", 1
    elif st and done_n >= max(st):
        stage, nxt = "finish", None
    else:
        stage, nxt = "build", done_n + 1
    ran = sp.ran(item)
    cost = {"low": 0, "medium": 1, "high": 2}
    want = STAGE_SKILLS[stage]
    tags = set(re.findall(r"\[([a-z-]+)\]", item["head"].lower()))
    # skills of the item's own kind first (a [security] item gets the security review before UI polish)
    skills = sorted((n for n in sp.fits(item) if n not in ran and any(w in sp.SKILLS[n][3] for w in want)),
                    key=lambda n: (sp.SKILLS[n][0] not in tags, cost[sp.SKILLS[n][4]]))[:3]
    return {"id": item["id"], "title": re.sub(r"^(\[[^\]]*\])+\s*", "", item["head"]), "stage": stage,
            "step": nxt, "step_text": (st.get(nxt, "") if st else " ".join(lines_of(item, "Do:"))) if nxt else "",
            "skills": [sp.SKILLS[n][1] for n in skills], "skill_nums": skills, "asks": open_asks(item)}


def prompt(p):
    """The copy-paste prompt for one plan_for() result."""
    out = ["/caveman", "{} {}".format(p["id"], p["title"]), ""]
    if p["stage"] == "plan":
        out.append("Stage: PLAN. Read the item, its Review/Answer lines and todo-code file. Audit the code in "
                   "scope with the skills below, write a short plan as a Review line, then build step 1.")
    elif p["stage"] == "build":
        out.append("Stage: BUILD step {}: {}".format(p["step"], p["step_text"] or "(see the Do line)"))
    else:
        out.append("Stage: FINISH. Every Do step is done. Re-run the skills that ran before, write the "
                   "before/after table into a Review line and the project walkthrough, then tick the item "
                   "and move it to TODO-DONE.md.")
    if p["skills"]:
        # The autopilot runs these itself: "/a:b args" -> Skill tool with skill="a:b", args="args".
        out.append("Skills, cheapest first (invoke each with the Skill tool; drop the leading /, text after a "
                   "space is the args): " + ", ".join(p["skills"]) +
                   " (record with: skills_prompt.py done {} {})".format(p["id"], ",".join(map(str, p["skill_nums"]))))
    if p["asks"]:
        out.append("Open questions (ask only if this step needs them, all at once; \"defaults\" = recommended):")
        out += [f"  {k}. {a}" for k, a in enumerate(p["asks"], 1)]
    out += ["", RULE]
    return "\n".join(out)


def first_items():
    """Open [first] items, then P1, in list order."""
    allx = [i for i in sp.items() if not i["done"]]
    return [i for i in allx if "[first]" in i["head"]] + [i for i in allx if "[P1]" in i["head"] and "[first]" not in i["head"]]


def main(argv):
    """CLI: T#### | first | questions, optional --json."""
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if not args:
        sys.exit(__doc__.split("\n\n")[1])
    what = args[0]
    if what == "questions":
        rows = [{"id": i["id"], "title": i["head"][:90], "asks": open_asks(i)} for i in first_items() if "[first]" in i["head"]]
        rows = [r for r in rows if r["asks"]]
        if as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            n = 0
            for r in rows:
                print("{} {}".format(r["id"], r["title"]))
                for a in r["asks"]:
                    n += 1
                    print(f"  {n}. {a}")
        return 0
    if what == "first":
        # Items another session owns, or that wait on Jason, are listed one per line in autopilot-skip.txt
        # ("T1014  photo session"); the autopilot (T1017 step 4) never picks them.
        skip_file = sp.LIST.parent / "autopilot-skip.txt"
        skip = set(re.findall(r"^(T\d{4,})", skip_file.read_text(encoding="utf-8"), re.MULTILINE)) if skip_file.exists() else set()
        pick = next((plan_for(i) for i in first_items() if i["id"] not in skip), None)
    else:
        found = [i for i in sp.items() if i["id"] == what]
        pick = plan_for(found[0]) if found else None
    if not pick:
        sys.exit("no open item: " + what)
    print(json.dumps(dict(pick, prompt=prompt(pick)), ensure_ascii=False, indent=1) if as_json else prompt(pick))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
