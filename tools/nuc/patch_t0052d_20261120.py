#!/usr/bin/env python3
"""patch_t0052d.py - T0052 step d / T0229 (2026-11-20 autopilot): one retry on Actual's bank sync,
mail only after two failed runs in a row, 'this morning'/'this evening' from the New York hour.
Writes money_daily.mjs.new next to the live file; never touches the live file (swap is a separate cp).
Usage: python3 patch_t0052d.py ~/money-autopilot/money_daily.mjs
"""
import sys
from pathlib import Path

src = Path(sys.argv[1])
text = src.read_text(encoding="utf-8")
old = ('try { await api.runBankSync(); } catch (e) { once("syncfail-" + today, "Bank sync didn\'t run", '
       '"Actual could not sync this morning: " + String(e.message || e).slice(0, 200)); }\n')
if text.count(old) != 1:
    sys.exit("anchor not found exactly once; nothing written")
new = '''// T0052d / T0229 (2026-11-20 autopilot): one retry after 45 s; mail only when two runs in a row failed.
// Before: one SimpleFIN/Actual timeout mailed "Bank sync didn't run" and always said "this morning".
// syncFails lives in state.json (saved at the end of this script). Trap: after a 07:30 UTC failure the
// first mail can come with the 18:00 UTC run. Test: SYNC_FAIL_TEST=1 forces a failure (no live state use).
let syncErr = null;
for (let tryNo = 1; tryNo <= 2; tryNo++) {
  try {
    if (process.env.SYNC_FAIL_TEST) throw new Error("forced test failure");
    await api.runBankSync(); syncErr = null; break;
  } catch (e) { syncErr = e; if (tryNo < 2) await new Promise(r => setTimeout(r, process.env.SYNC_FAIL_TEST ? 10 : 45000)); }
}
state.syncFails = syncErr ? (state.syncFails || 0) + 1 : 0;
const dayPart = Number(new Date().toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", hour12: false })) < 12 ? "this morning" : "this evening";
if (syncErr && state.syncFails >= 2) once("syncfail-" + today, "Bank sync didn't run",
  `Actual could not sync ${dayPart} (twice in a row): ` + String(syncErr.message || syncErr).slice(0, 200));
'''
out = src.with_name(src.name + ".new")
out.write_text(text.replace(old, new), encoding="utf-8")
print("wrote", out)
