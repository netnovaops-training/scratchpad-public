#!/bin/bash
# test_t0052d.sh - T0052d / T0229 retry block, tested with a stub api (no Actual, no live state.json).
# Pulls the new block out of money_daily.mjs.new and runs it 4 times: fail, fail, ok, fail-once-then-ok.
set -e
cd ~/sandbox && mkdir -p t0052d && cd t0052d
NEW=~/money-autopilot/money_daily.mjs.new
node --check "$NEW" && echo "check: node --check OK"
sed -n '/T0052d \/ T0229/,/twice in a row/p' "$NEW" > block.js
cat > run.mjs <<'EOF'
import fs from "node:fs";
const block = fs.readFileSync("block.js", "utf8");
const today = "2026-11-20";
const fn = new (Object.getPrototypeOf(async function(){}).constructor)("api", "state", "once", "today", "process", block);
async function run(name, state, failPlan) {
  const msgs = []; let calls = 0;
  const once = (k, t, b) => { if (state.sent[k]) return; msgs.push(t + " | " + b); state.sent[k] = today; };
  const api = { runBankSync: async () => { if (failPlan[calls++]) throw new Error("timeout"); } };
  const proc = { env: { SYNC_FAIL_TEST: "" } };
  await fn(api, state, once, today, proc);
  console.log(name, "calls=" + calls, "syncFails=" + state.syncFails, "mails=" + msgs.length, msgs[0] || "");
}
const s = { sent: {} };
await run("run1 both tries fail:", s, [1, 1]);
await run("run2 both tries fail:", s, [1, 1]);
await run("run3 sync ok:      ", s, [0]);
const s2 = { sent: {} };
await run("retry saves it:    ", s2, [1, 0]);
EOF
sed -i 's/45000/45/' block.js   # speed only; the live file keeps 45000
node run.mjs
