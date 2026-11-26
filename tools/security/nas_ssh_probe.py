#!/usr/bin/env python3
"""nas_ssh_probe.py - READ-ONLY NAS probe over SSH (root via photos_syno/nas.py script()): volumes, shares,
snapshot CLI help and the Snapshot Replication / Antivirus web API names. Prints no config values.
Run:  python D:\\scratchpad\\tools\\security\\nas_ssh_probe.py
OUTCOME 2026-11-24: DSM API storage load_info timed out while the photo job ran; SSH used instead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "photos_syno"))
from nas import script  # noqa: E402

CMD = r"""
echo ===DF; df -hT /volume* 2>/dev/null | grep -v tmpfs
echo ===SHARES; for s in homes Paperless private videos Downloads Pictures photo; do
  p=$(ls -d /volume*/$s 2>/dev/null | head -1); [ -n "$p" ] && echo "$s $p $(btrfs subvolume show $p >/dev/null 2>&1 && echo subvol || echo not-subvol)"; done
echo ===SNAPCLI; ls /usr/syno/sbin/ | grep -i snap; /usr/syno/sbin/synosharesnapshot 2>&1 | head -40
echo ===SNAPAPI; ls /var/packages/SnapshotReplication/target/webapi/ 2>/dev/null; grep -ho '"SYNO[^"]*"' /var/packages/SnapshotReplication/target/webapi/*.lib 2>/dev/null | sort -u | head -60
echo ===AVAPI; grep -ho '"SYNO[^"]*"' /var/packages/AntiVirus/target/webapi/*.lib /var/packages/AntiVirus/target/ui/*.lib 2>/dev/null | sort -u | head -40; ls /var/packages/AntiVirus/target/ 2>/dev/null
echo ===EXISTING_SNAPS; for s in homes Paperless private videos Downloads Pictures; do echo "$s: $(/usr/syno/sbin/synosharesnapshot list $s 2>/dev/null | grep -c GMT)"; done
echo ===CRON; grep -i -E 'snap|antivirus|av_' /etc/crontab 2>/dev/null | head
echo ===TASKS; ls /usr/syno/etc/synoschedule.d/ 2>/dev/null | head; ls /usr/syno/etc/synoschedule.d/* 2>/dev/null | head -30
"""

if __name__ == "__main__":
    rc, out, err = script(CMD, as_root=True, timeout=300)
    print(out)
    if rc:
        print("rc", rc, err[-400:])
