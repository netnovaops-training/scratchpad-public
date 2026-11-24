#!/usr/bin/env python3
"""nas_api_defs.py - READ-ONLY: print the DSM web API definitions (methods/versions) for the snapshot and
antivirus APIs from the NAS's .lib files, and any existing AV schedule / snapshot plan, so the scheduler can call
the right methods. Prints API names, method names and schedule fields only.
OUTCOME 2026-11-24: written for item 2 of the approved security fixes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "photos_syno"))
from nas import script  # noqa: E402

CMD = r"""
cd /usr/syno/synoman/webapi 2>/dev/null && for f in $(grep -l 'Share.Snapshot\|SYNO.Core.Share.Snapshot\|Snapshot.Plan\|SnapshotPlan' *.lib 2>/dev/null); do echo "== $f"; python3 -c "
import json,sys
d=json.load(open('$f'))
for k,v in d.items():
  if 'napshot' in k:
    print(k, v.get('minVersion'), v.get('maxVersion'), [m for vv in (v.get('methods') or {}).values() for m in (vv if isinstance(vv,list) else [vv])][:40])
" 2>&1 | head -20; done
echo "== AV lib"
for f in /var/packages/AntiVirus/target/webapi/*.lib; do python3 -c "
import json
d=json.load(open('$f'))
for k,v in d.items():
  if 'Schedule' in k or 'Scan' in k or 'Config' in k: print(k, v.get('minVersion'), v.get('maxVersion'), v.get('methods'))
" 2>&1 | head -20; done
echo "== snapshot plans in share confs"; ls /usr/syno/etc/synosnapshot* /usr/syno/etc/packages/SnapshotReplication/ 2>/dev/null
for i in 1 2 3 4 5 6 7 8; do f=/usr/syno/etc/synoschedule.d/root/$i.task; [ -f $f ] && grep -E '^(name|app|cmd|owner|state|repeat|week|hour|min)' $f | sed 's/^cmd=.*/cmd=<omitted>/' | tr '\n' ' ' | sed -E 's/[[:alnum:]._+-]+@[[:alnum:].-]+/<email>/g' && echo; done
"""

if __name__ == "__main__":
    rc, out, err = script(CMD, as_root=True, timeout=200)
    print(out)
    if rc:
        print("rc", rc, err[-400:])
