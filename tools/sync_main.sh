#!/usr/bin/env bash
# sync_main.sh - bring `main` up to date in every clone on this PC, fast-forward only (2026-11-19).
#
#   bash D:/scratchpad/tools/sync_main.sh          (run by the resume-work skill at session start)
#
# Why: Jason 2026-11-19: "Pull the latest code when a session starts". Only `main` moves, and only by
# fast-forward: a clone whose main has local commits GitHub lacks is reported, never merged or reset.
# Feature branches are never touched. If main is the checked-out branch and the tree has changes, it is
# skipped (a pull could touch his files). Prints one line per clone.
set -u
CLONES=(
  /d/scratchpad
  /d/scratchpad-public
  "/c/Users/USER/Projects/Projects-repo"
  /c/Users/USER/Projects/garden-for-all-rust
  /c/Users/USER/Projects/garden-for-all-website
  /c/Users/USER/Projects/robin-photo-backup
  /c/Users/USER/AndroidStudioProjects/SproutPals
  /c/Users/USER/AndroidStudioProjects/SproutPals-deps
  /c/Users/USER/AndroidStudioProjects/SproutPalsApple
)
for d in "${CLONES[@]}"; do
  [ -d "$d/.git" ] || [ -f "$d/.git" ] || { echo "skip  $d (not here)"; continue; }
  r=$(git -C "$d" remote -v | awk '/github.com\/netnovaops-training/ {print $1; exit}')
  [ -n "$r" ] || { echo "skip  $d (no GitHub remote)"; continue; }
  before=$(git -C "$d" rev-parse --short main 2>/dev/null || echo none)
  cur=$(git -C "$d" branch --show-current)
  if [ "$cur" = main ]; then
    if [ -n "$(git -C "$d" status --porcelain --untracked-files=no)" ]; then
      echo "skip  $d (on main with uncommitted changes)"; continue
    fi
    out=$(git -C "$d" pull -q --ff-only "$r" main 2>&1); rc=$?
  else
    # OUTCOME 2026-11-19: with -q a refused (non-fast-forward) fetch printed nothing, so the exit code
    # is what tells "diverged" apart from "nothing new" (SproutPalsApple main: 15 local, 41 behind).
    out=$(git -C "$d" fetch -q "$r" main:main 2>&1); rc=$?
  fi
  after=$(git -C "$d" rev-parse --short main 2>/dev/null || echo none)
  if [ "$rc" -ne 0 ]; then
    ahead=$(git -C "$d" rev-list --count "$r/main..main" 2>/dev/null || echo ?)
    echo "WARN  $d: main $before not moved (has $ahead commit(s) GitHub lacks) ${out:+- $(echo "$out" | head -1)}"
  elif [ "$before" = "$after" ]; then echo "same  $d: main $after"
  else echo "moved $d: main $before -> $after"; fi
done
