#!/usr/bin/env python3
"""nas_dsm_prom.py - the Synology NAS's disks, SMART, storage pool (RAID) and SSD cache state for Prometheus.

Why (2026-11-24): Jason expects a hardware failure within a month. The NAS (DS at 192.168.1.68, Tailscale
100.64.0.12) went "volume1 read-write SSD cache degraded" at 04:03 on 11-24 (nvme1n1 missing). Nothing on the
NUC watched it: the prometheus.yml "nas" job has been commented out since 2026-09.
Picked over SNMP because it needs NO new port and no DSM change: it talks to DSM's own web API on 5001 over
Tailscale (already open). SNMP would open UDP 161 on the NAS and need DSM clicks plus a firewall rule.

How to run:  cron (user server) */15: python3 ~/hwmon/nas_dsm_prom.py
             python3 ~/hwmon/nas_dsm_prom.py --print      also print the metrics
             python3 ~/hwmon/nas_dsm_prom.py --discover   print the API's field names (no secrets) and exit
Login:       Jason's DSM account from Proton Pass (vault "Rotated", item "davetta1.us1.quickconnect.to"), read at
             run time through ~/bin/pass into memory only, sent in a POST body (never a URL), logged out at the end.
             Never printed, never written. DSM's cert is self-signed, so it is pinned by fingerprint on first use
             (~/hwmon/nas-cert.sha256) and the password is not sent if it changes; the path is Tailscale too.
Writes:      ~/hwmon/textfile/nas.prom (node-exporter textfile collector, job "nuc"):
             hw_smart{machine="nas",drive,serial,model,kind,field}   same names as the PC and NUC drives
             hw_nas_state{kind="pool|cache|volume|disk",name,status} 1 for the current status
             hw_nas_ok 1 when this run read the NAS
             ~/hwmon/nas-last.json   the last run's readable summary (hw_watch.py reads it)
Reads only:  SYNO.Storage.CGI.Storage load_info and SYNO.Storage.CGI.Smart get_smart_info. Changes nothing.
OUTCOME 2026-11-24: written; see the commit for the first live run.
"""
import hashlib
import http.client
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

H = Path.home()
NAS_HOST, NAS_PORT, NAS_PATH = "100.64.0.12", 5001, "/webapi/entry.cgi"
OUT = H / "hwmon" / "textfile" / "nas.prom"
LAST = H / "hwmon" / "nas-last.json"
PASS = [str(H / "bin" / "pass"), "item", "view", "--vault-name", "Rotated", "--item-title", "davetta1.us1.quickconnect.to"]
PIN = H / "hwmon" / "nas-cert.sha256"
# Self-signed DSM certificate: chain checks are off, and call() compares its fingerprint instead, on the SAME
# connection that then carries the request (audit 2026-11-24 Low #17: the pin used to be checked on a separate socket).
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
# DSM SMART attribute id -> our field name (same as the PC's ATA list)
ATA = {5: "reallocated", 197: "pending", 198: "uncorrectable", 187: "reported_uncorrect", 199: "crc_errors",
       193: "load_unload", 4: "start_stop", 10: "spin_retry", 9: "power_on_hours", 194: "temp_raw"}


def secret(field: str) -> str:
    r = subprocess.run(PASS + ["--field", field], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


class PinError(RuntimeError):
    """The NAS certificate is not the pinned one: nothing was sent on that connection."""


def check_pin(der: bytes) -> None:
    """DSM's certificate is self-signed, so normal verification cannot pass. Instead its SHA-256 fingerprint is
    pinned on first use (~/hwmon/nas-cert.sha256) and every later request refuses to send anything if it changes.
    A renewed DSM certificate trips this once: delete the pin file (to trash) after checking DSM renewed it."""
    fp = hashlib.sha256(der).hexdigest()
    if not PIN.exists():
        PIN.parent.mkdir(parents=True, exist_ok=True)
        PIN.write_text(fp + "\n")
    elif PIN.read_text().strip() != fp:
        raise PinError("NAS certificate changed since it was pinned; not sending the request (see check_pin)")


def call(params: dict, timeout=30) -> dict:
    """POST to DSM; returns the JSON. The TLS connection is opened first, its certificate is checked against the
    pin, and only then is the body (which may hold the password or sid) sent on that same connection.
    Errors name the API only, never the body.
    OUTCOME 2026-11-24 (audit fix #17): replaces urlopen plus a separate check_pin socket."""
    data = urllib.parse.urlencode(params).encode()
    conn = http.client.HTTPSConnection(NAS_HOST, NAS_PORT, timeout=timeout, context=CTX)
    try:
        conn.connect()
        check_pin(conn.sock.getpeercert(binary_form=True))
        conn.request("POST", NAS_PATH, body=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(conn.getresponse().read())
    except PinError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"{params.get('api')} {params.get('method')}: {type(ex).__name__}") from None
    finally:
        conn.close()


def login() -> str:
    user = secret("username") or secret("email")
    pw = secret("password")
    if not user or not pw:
        raise RuntimeError("DSM login not readable from Pass (is ~/bin/pass logged in?)")
    j = call({"api": "SYNO.API.Auth", "version": "6", "method": "login", "account": user, "passwd": pw,
              "session": "hwmon", "format": "sid"})
    del pw
    if not j.get("success"):
        code = (j.get("error") or {}).get("code")
        hint = {400: "wrong account or password", 401: "account disabled", 402: "permission denied",
                403: "2-step verification code required", 404: "2-step verification failed",
                407: "IP blocked", 410: "password expired"}.get(code, "")
        raise RuntimeError(f"DSM login refused (error {code} {hint})")
    return j["data"]["sid"]


def esc(s) -> str:
    return str(s).replace("\\", "").replace('"', "").replace("\n", " ").strip()


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def shape(o, depth=0, key=""):
    """--discover: keys and value TYPES (plus short status-like strings), never long values."""
    pad = "  " * depth
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}:")
                shape(v, depth + 1, k)
            else:
                show = v if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 24) else type(v).__name__
                print(f"{pad}{k} = {show!r}")
    elif isinstance(o, list):
        print(f"{pad}[{len(o)} items]")
        if o:
            shape(o[0], depth + 1, key)


def main() -> int:
    sid = login()
    try:
        info = call({"api": "SYNO.Storage.CGI.Storage", "version": "1", "method": "load_info", "_sid": sid}, 170)
        if not info.get("success"):
            raise RuntimeError(f"load_info failed: {info.get('error')}")
        d = info["data"]
        smart = {}
        for disk in d.get("disks", []):
            # the parameter is "device" (tested 11-24: "disk" gives error 114, missing parameter)
            s = call({"api": "SYNO.Storage.CGI.Smart", "version": "1", "method": "get_smart_info",
                      "device": disk.get("device") or f"/dev/{disk.get('id')}", "_sid": sid})
            smart[disk.get("id")] = s.get("data") if s.get("success") else None
        if "--discover" in sys.argv:
            shape({k: v for k, v in d.items() if k in ("disks", "storagePools", "ssdCaches", "volumes", "env")})
            print("--- smart (first disk) ---")
            shape(next(iter(smart.values()), None))
            return 0
    finally:
        try:
            call({"api": "SYNO.API.Auth", "version": "6", "method": "logout", "session": "hwmon", "_sid": sid}, 10)
        except RuntimeError:
            pass

    lines = ["# HELP hw_smart SMART health per drive (hw_pc_smart.ps1 / nuc_smart_prom.py)", "# TYPE hw_smart gauge",
             "# HELP hw_nas_state 1 = this pool/cache/volume/disk currently has this status", "# TYPE hw_nas_state gauge",
             "# HELP hw_nas_ok 1 when the last run read the NAS", "# TYPE hw_nas_ok gauge"]
    summary = {"at": int(time.time()), "disks": [], "states": []}
    for disk in d.get("disks", []):
        name = disk.get("longName") or disk.get("name") or disk.get("id")
        kind = "ssd" if disk.get("isSsd") or "nvme" in str(disk.get("id", "")) else "hdd"
        lab = (f'machine="nas",drive="{esc(name)}",serial="{esc(disk.get("serial", ""))}",'
               f'model="{esc(disk.get("model", ""))}",kind="{kind}"')
        f = {}
        if num(disk.get("temp")) is not None:
            f["temp_c"] = num(disk["temp"])
        f["passed"] = 1 if str(disk.get("smart_status", "normal")).lower() in ("normal", "safe", "") else 0
        for a in (smart.get(disk.get("id")) or {}).get("smartInfo") or []:
            aid = int(num(a.get("id")) or 0)
            raw = num(str(a.get("raw", "")).split()[0] if a.get("raw") else None)
            if aid in ATA and raw is not None and ATA[aid] != "temp_raw":
                f.setdefault(ATA[aid], raw)
        if (num(disk.get("unc")) or -1) >= 0:   # DSM's own count, when no attribute 198 (-1 = not reported, SSDs)
            f.setdefault("uncorrectable", num(disk["unc"]))
        life = (disk.get("remain_life") or {}).get("value") if isinstance(disk.get("remain_life"), dict) else disk.get("remain_life")
        if num(life) is not None and num(life) >= 0:   # -1 = not reported (hard drives)
            f["percentage_used"] = 100 - num(life)
        for k, v in f.items():
            lines.append(f'hw_smart{{{lab},field="{k}"}} {v:g}')
        st = str(disk.get("status", "unknown"))
        lines.append(f'hw_nas_state{{kind="disk",name="{esc(name)}",status="{esc(st)}"}} 1')
        summary["disks"].append({"name": name, "status": st, **f})
        summary["states"].append({"kind": "disk", "name": name, "status": st})
    for kind, key in (("pool", "storagePools"), ("cache", "ssdCaches"), ("volume", "volumes")):
        for p in d.get(key, []) or []:
            name = (f"SSD cache {p.get('path', '')}".strip() if kind == "cache" else
                    f"volume {p.get('vol_path', '')}".strip() if kind == "volume" else
                    f"Storage Pool {p['num_id']}" if p.get("num_id") else p.get("id")) or p.get("id") or "?"
            st = str(p.get("status", "unknown"))
            if kind == "cache":   # how many cache SSDs are working vs designed (11-24: 1 of 2, nvme1n1 missing)
                for r in p.get("raids", []) or []:
                    lines.append(f'hw_nas_cache_devices{{name="{esc(name)}",which="normal"}} {num(r.get("normalDevCount")) or 0:g}')
                    lines.append(f'hw_nas_cache_devices{{name="{esc(name)}",which="designed"}} {num(r.get("designedDiskCount")) or 0:g}')
                    st += f" ({r.get('normalDevCount')}/{r.get('designedDiskCount')} SSDs)"
            lines.append(f'hw_nas_state{{kind="{kind}",name="{esc(name)}",status="{esc(st)}"}} 1')
            summary["states"].append({"kind": kind, "name": name, "status": st})
    lines.append(f'hw_nas_disk_count {len(d.get("disks", []))}')
    lines.append("hw_nas_ok 1")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((OUT, "\n".join(lines) + "\n"), (LAST, json.dumps(summary, indent=1))):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
    if "--print" in sys.argv:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as ex:
        # a failed read: keep the old numbers out (they would look current) and say so
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("# HELP hw_nas_ok 1 when the last run read the NAS\n# TYPE hw_nas_ok gauge\nhw_nas_ok 0\n")
        print(time.strftime("%F %T"), "nas_dsm_prom:", ex, flush=True)
        sys.exit(1)
