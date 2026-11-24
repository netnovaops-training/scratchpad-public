#!/usr/bin/env python3
"""nuc_smart_prom.py - the NUC's own drives' SMART health for Prometheus (2026-11-24).

Why: Jason, 11-24, expects a hardware failure within a month and wants every drive watched. The NUC has two:
  nvme0  "512GB SSD"            the system disk (/, /boot)
  sda    NVMe in a Best Buy NS-PCNVMEHDE USB enclosure (USB id 6655:a583): /mnt/vault, the LUKS photo vault
smartctl --scan does not list sda (USB bridge); the bridge type is found by trying the NVMe-over-USB types
smartmontools knows, and the one that works is remembered in /var/lib/hwmon/<dev>.type.

How to run:  as root (SMART needs it): /etc/cron.d/hwmon-smart runs /usr/local/sbin/nuc_smart_prom.py every 5 min.
             Installed by nuc_hwmon_root_setup.sh (Jason, sudo, once). Test: sudo python3 nuc_smart_prom.py --print
             Test without root: python3 nuc_smart_prom.py --fixture <dir with nvme0.json, sda.json>
Writes:      /home/user/hwmon/textfile/nuc_smart.prom  (node-exporter textfile collector, job "nuc")
             metric hw_smart{machine="nuc",drive,serial,model,kind,field}, the same names as the PC's hw_pc_smart.ps1
Reads only:  smartctl -j -H -A -i. No self-test, no setting changed.
OUTCOME 2026-11-24: written; tested with --fixture. Real run waits for the sudo setup.
Audit fixes 2026-11-24: (#20) smartctl is called by its full path /usr/sbin/smartctl, so cron's short PATH
             cannot hide it. (#4) the output folder is owned by "server", so the write no longer follows any
             symlink: every folder on the path is opened with O_NOFOLLOW and the temp file with
             O_CREAT|O_EXCL|O_NOFOLLOW, mode set on the open file. Tested with a symlinked tmp and folder.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

OUT = Path("/home/user/hwmon/textfile/nuc_smart.prom")
SMARTCTL = "/usr/sbin/smartctl"  # full path: cron PATH is /usr/bin:/bin (audit #20)
STATE = Path("/var/lib/hwmon")
# (device, label, kind, smartctl -d types to try in order)
DRIVES = [("/dev/nvme0", "system", "nvme", ["nvme"]),
          ("/dev/sda", "vault", "nvme", ["sntrealtek", "sntjmicron", "sntasmedia", "sat", "auto"])]
ATA = {5: "reallocated", 197: "pending", 198: "uncorrectable", 187: "reported_uncorrect", 199: "crc_errors",
       193: "load_unload", 4: "start_stop", 10: "spin_retry", 184: "end_to_end"}
NVME = {"media_errors": "media_errors", "percentage_used": "percentage_used", "available_spare": "available_spare",
        "critical_warning": "critical_warning", "unsafe_shutdowns": "unsafe_shutdowns",
        "num_err_log_entries": "err_log_entries", "data_units_written": "data_units_written"}


def fields(j: dict) -> dict:
    """smartctl JSON -> {field: number}. Missing sections give fewer fields, never a fake 0."""
    f = {}
    if "smart_status" in j:
        f["passed"] = int(bool(j["smart_status"].get("passed")))
    if (j.get("temperature") or {}).get("current") is not None:
        f["temp_c"] = j["temperature"]["current"]
    if (j.get("power_on_time") or {}).get("hours") is not None:
        f["power_on_hours"] = j["power_on_time"]["hours"]
    for a in (j.get("ata_smart_attributes") or {}).get("table", []):
        if a.get("id") in ATA:
            f[ATA[a["id"]]] = a["raw"]["value"]
    n = j.get("nvme_smart_health_information_log") or {}
    for k, name in NVME.items():
        if n.get(k) is not None:
            f[name] = n[k]
    return f


def read(dev: str, types: list, fixture: str):
    """Return (json, type) for the first -d type that yields SMART fields, else (None, None)."""
    if fixture:
        p = Path(fixture) / (Path(dev).name + ".json")
        return (json.loads(p.read_text()), "fixture") if p.exists() else (None, None)
    known = STATE / (Path(dev).name + ".type")
    order = ([known.read_text().strip()] if known.exists() else []) + types
    for t in dict.fromkeys(order):
        r = subprocess.run([SMARTCTL, "-j", "-H", "-A", "-i", "-d", t, dev], capture_output=True, text=True, timeout=60)
        try:
            j = json.loads(r.stdout)
        except ValueError:
            continue
        if len(fields(j)) >= 2:
            STATE.mkdir(parents=True, exist_ok=True)
            known.write_text(t)
            return j, t
    return None, None


def esc(s) -> str:
    return str(s).replace("\\", "").replace('"', "").replace("\n", " ").strip()


def open_dir_nofollow(path: Path) -> int:
    """Open every folder of an absolute path with O_NOFOLLOW, so a symlink anywhere on it is refused.
    The textfile folder and its parents under /home/user are writable by "server"; this job runs as root."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_safely(out: Path, text: str) -> None:
    """Write out.tmp then rename it over out, all relative to a no-follow folder handle (audit #4).
    O_EXCL fails if anything (a planted symlink included) already sits at the temp name; a leftover temp from a
    crashed run is unlinked first (unlink removes a symlink itself, never its target)."""
    d = open_dir_nofollow(out.parent)
    tmp = out.name + ".tmp"
    try:
        try:
            os.unlink(tmp, dir_fd=d)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=d)
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o644)
            f.write(text)
        os.replace(tmp, out.name, src_dir_fd=d, dst_dir_fd=d)  # whole-file swap: node-exporter never reads half
    finally:
        os.close(d)


def main() -> int:
    fixture = sys.argv[sys.argv.index("--fixture") + 1] if "--fixture" in sys.argv else ""
    out = Path(fixture).resolve() / "nuc_smart.prom" if fixture else OUT
    lines = ["# HELP hw_smart SMART health per drive (hw_pc_smart.ps1 / nuc_smart_prom.py)", "# TYPE hw_smart gauge",
             "# HELP hw_smart_read_ok 1 when the drive's SMART was read this run", "# TYPE hw_smart_read_ok gauge"]
    for dev, label, kind, types in DRIVES:
        j, _ = read(dev, types, fixture)
        base = f'machine="nuc",drive="{label}"'
        if j is None:
            lines.append(f'hw_smart_read_ok{{{base},dev="{dev}"}} 0')
            continue
        lab = f'{base},serial="{esc(j.get("serial_number", ""))}",model="{esc(j.get("model_name", ""))}",kind="{kind}"'
        for k, v in fields(j).items():
            lines.append(f'hw_smart{{{lab},field="{k}"}} {v}')
        lines.append(f'hw_smart_read_ok{{{base},dev="{dev}"}} 1')
    write_safely(out, "\n".join(lines) + "\n")
    if "--print" in sys.argv or fixture:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
