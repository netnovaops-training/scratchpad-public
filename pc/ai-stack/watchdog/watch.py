#!/usr/bin/env python3
"""
Watch the other machine, and say so on Telegram when it stops answering.

WHY THIS EXISTS AND WHY IT IS NOT UPTIME KUMA.
Uptime Kuma runs ON the NUC. When the NUC was unplugged on 2026-08-29 it went
down with it and sent nothing -- Jason found out by clicking a link that did
not work. A watchdog cannot live on the machine it watches. That is the whole
design, and it is why this is a separate tiny service on the desktop rather
than another check added to the Kuma that already exists.

WHAT IT CANNOT DO, STATED PLAINLY.
This runs on SuperNova. If SuperNova dies, or the house loses power, or the
internet goes down, nothing here will tell you -- the alerter is inside the
thing it would need to report on. The only fix for that is a check from
outside the house entirely (healthchecks.io, UptimeRobot and similar are free
at this size). This covers the case that actually happened and is honest about
the case it does not.

WHAT IT CHECKS.
A TCP connect to each target. Not ICMP: the NUC answered ping perfectly while
every service on it was dead, which is exactly the failure that started this.
Ping tells you a kernel is alive; a TCP connect tells you something is
listening.

WHEN IT SPEAKS.
Only on a CHANGE of state, never on a schedule. A monitor that messages you
every five minutes to say everything is fine is a monitor you mute, and a
muted monitor is worse than none. Two consecutive failures are required before
it calls something down, so one dropped packet during a reboot does not send
a false alarm.
"""

import datetime
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if c.strip()]
INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "60"))
TIMEOUT = float(os.environ.get("WATCHDOG_TIMEOUT_SECONDS", "6"))
# How many consecutive failures before it is called down. Two at a 60s
# interval means a real outage is reported inside about two minutes, while a
# single blip during a container restart is ignored.
FAILURES_BEFORE_DOWN = int(os.environ.get("WATCHDOG_FAILURES", "2"))

# A DEAD MAN'S SWITCH, and the only thing here that can report on the house
# as a whole. Everything else in this file runs inside the estate it watches:
# if this machine dies, or the power or the internet goes, nothing above will
# say so, because the alerter went with it.
#
# So this pings OUT to a service that expects to hear from us. When the pings
# stop, THEY raise the alarm -- silence becomes the signal, which is the only
# shape that survives the alerter being dead.
#
# Empty until a URL is pasted in; the feature is simply off until then.
HEARTBEAT_URL = os.environ.get("WATCHDOG_HEARTBEAT_URL", "").strip()

# OFF BY DEFAULT. It used to announce itself on Telegram every time it
# started, which meant a message on every rebuild -- and Jason asked for
# messages only when something breaks. Silence is now the healthy state.
#
# The startup line still goes to the container log either way, so "is it
# running" is answerable without making the phone buzz. Set this to 1 for one
# restart if you ever want to prove the Telegram path still works end to end.
ANNOUNCE_START = os.environ.get("WATCHDOG_ANNOUNCE_START", "").strip() in ("1", "true", "yes")

# name, host, port. Each is a thing whose absence means something different:
# the box itself, then a service that proves Docker is actually serving.
TARGETS = []
for spec in os.environ.get("WATCHDOG_TARGETS", "").split(","):
    spec = spec.strip()
    if not spec:
        continue
    try:
        name, host, port = spec.split(":")
        TARGETS.append((name.strip(), host.strip(), int(port)))
    except ValueError:
        print(f"watchdog: cannot read target {spec!r}, expected name:host:port", flush=True)

if not TARGETS:
    print("watchdog: no WATCHDOG_TARGETS set; nothing to watch", flush=True)
    sys.exit(1)


def alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            return True
    except OSError:
        return False


def tell(text: str) -> None:
    """
    Send to Telegram. Never raises: a watchdog that dies because the network
    it is watching is down is not a watchdog.
    """
    if not BOT_TOKEN or not CHAT_IDS:
        print(f"watchdog (telegram not configured): {text}", flush=True)
        return
    for chat in CHAT_IDS:
        try:
            data = urllib.parse.urlencode({
                "chat_id": chat,
                "text": text,
                "disable_web_page_preview": "true",
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
        except Exception as e:  # noqa: BLE001 -- see the docstring
            print(f"watchdog: could not reach Telegram: {e}", flush=True)


def beat() -> None:
    """
    Tell the outside world we are still here.
    """
    if not HEARTBEAT_URL:
        return
    try:
        with urllib.request.urlopen(HEARTBEAT_URL, timeout=10) as r:
            r.read()
    except Exception as e:  # noqa: BLE001
        # Never fatal. A heartbeat that cannot get out is exactly the
        # situation it exists to report, and the reporting is the far end's
        # job -- our part is to stop pinging, which has already happened.
        print(f"watchdog: heartbeat failed: {e}", flush=True)

# --- added 2026-09-06: containers, backup freshness, and a daily digest ------
#
#  Jason asked for "any errors" across the dockers and for cost pushed to
#  Telegram. Everything here follows the two rules this file already had:
#  the standard library only, and alerts on CHANGE rather than on state.

DOCKER_API = os.environ.get("WATCHDOG_DOCKER_API", "").strip().rstrip("/")
LITELLM_URL = os.environ.get("WATCHDOG_LITELLM_URL", "").strip().rstrip("/")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "").strip()
BACKUP_MANIFEST = os.environ.get("WATCHDOG_BACKUP_MANIFEST",
                                 "/backup-status/manifest.json")
DIGEST_HOUR = int(os.environ.get("WATCHDOG_DIGEST_HOUR", "8"))
# Electricity is MODELLED, not measured -- there is no meter on either
# machine. Steady-state draw x hours x unit price, right to about a third.
# Enough to answer "is this expensive", not enough to put in a budget, and
# labelled that way everywhere it is shown.
PC_WATTS = float(os.environ.get("WATCHDOG_PC_WATTS", "140"))
NUC_WATTS = float(os.environ.get("WATCHDOG_NUC_WATTS", "25"))
KWH_PRICE = float(os.environ.get("WATCHDOG_KWH_PRICE", "0.24"))

# Containers that exit and restart by design, so their absence is not news.
TRANSIENT = {c.strip() for c in
             os.environ.get("WATCHDOG_TRANSIENT", "nextcloud_ai").split(",")
             if c.strip()}


def _get_json(url, headers=None, timeout=None):
    """urllib, not httpx -- see this file's Dockerfile for why."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT * 4) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def containers() -> dict:
    """name -> what is wrong with it. Empty means all well.

    Reads a socket PROXY, never the Docker socket itself. A container
    holding the real socket is root on the host, and being told which
    containers are unhappy is not worth that.
    """
    if not DOCKER_API:
        return {}
    try:
        rows = _get_json(f"{DOCKER_API}/containers/json?all=1")
    except Exception as e:  # noqa: BLE001
        return {"docker": f"could not be read ({e.__class__.__name__})"}
    out = {}
    for c in rows:
        name = (c.get("Names") or ["?"])[0].lstrip("/")
        if name in TRANSIENT:
            continue
        state = c.get("State") or ""
        status = c.get("Status") or ""
        if state != "running":
            out[name] = f"is {state}"
        elif "unhealthy" in status:
            out[name] = "is unhealthy"
        elif "Restarting" in status:
            out[name] = "is restarting"
    return out


def backup_age() -> dict:
    """Stale, or written without verifying the encrypted drive."""
    try:
        with open(BACKUP_MANIFEST, encoding="utf-8") as fh:
            m = json.load(fh)
    except FileNotFoundError:
        return {"backup": "has never run"}
    except Exception:  # noqa: BLE001
        return {"backup": "left an unreadable manifest"}
    if not m.get("vault_verified"):
        return {"backup": "did NOT verify the encrypted drive -- it stopped "
                          "rather than risk writing personal files somewhere "
                          "unencrypted"}
    hours = (time.time() - float(m.get("finished_at") or 0)) / 3600
    if hours > 48:
        return {"backup": "last ran %.1f days ago" % (hours / 24)}
    return {}


def spend():
    """What the models actually billed. Local models are $0, correctly."""
    out = {"today": 0.0, "month": 0.0, "calls": 0, "by_model": {}}
    if not (LITELLM_URL and LITELLM_KEY):
        return out
    try:
        rows = _get_json(f"{LITELLM_URL}/spend/logs",
                         {"Authorization": "Bearer " + LITELLM_KEY},
                         timeout=60)
    except Exception as e:  # noqa: BLE001
        out["error"] = e.__class__.__name__
        return out
    now = datetime.datetime.now(datetime.timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mon0 = day0.replace(day=1)
    for row in rows if isinstance(rows, list) else []:
        try:
            when = datetime.datetime.fromisoformat(
                str(row.get("startTime") or "")[:19]).replace(
                    tzinfo=datetime.timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        v = float(row.get("spend") or 0)
        if when >= mon0:
            out["month"] += v
        if when >= day0:
            out["today"] += v
            out["calls"] += 1
            m = row.get("model") or "?"
            out["by_model"][m] = out["by_model"].get(m, 0.0) + v
    return out


def digest(problems: dict) -> str:
    s = spend()
    power = ((PC_WATTS + NUC_WATTS) / 1000.0) * 24 * KWH_PRICE
    lines = ["Daily digest", ""]
    if problems:
        lines.append("Still wrong (%d):" % len(problems))
        lines += ["  - %s %s" % (k, v) for k, v in sorted(problems.items())]
    else:
        lines.append("Everything is up.")
    lines += ["",
              "Models billed today: $%.4f (%d calls)" % (s["today"], s["calls"]),
              "This month so far:   $%.2f" % s["month"]]
    for m, v in sorted(s.get("by_model", {}).items(), key=lambda kv: -kv[1])[:4]:
        lines.append("   %s: $%.4f" % (m, v))
    if s.get("error"):
        lines.append("   (could not read LiteLLM: %s)" % s["error"])
    lines += ["",
              "Electricity, ESTIMATED: ~$%.2f/day. There is no meter -- this "
              "assumes %.0fW + %.0fW at $%.2f/kWh, so treat it as a rough "
              "figure. Local models cost this rather than tokens."
              % (power, PC_WATTS, NUC_WATTS, KWH_PRICE)]
    try:
        with open(BACKUP_MANIFEST, encoding="utf-8") as fh:
            m = json.load(fh)
        hours = (time.time() - float(m.get("finished_at") or 0)) / 3600
        lines.append("Backup: %.0fh ago, %s" % (
            hours, "encrypted drive verified" if m.get("vault_verified")
            else "NOT VERIFIED"))
    except Exception:  # noqa: BLE001
        lines.append("Backup: no manifest found.")
    return "\n".join(lines)


def main() -> None:
    # Everything starts assumed-up, so a restart of THIS service does not
    # announce a recovery for something that never went away.
    state = {name: True for name, _, _ in TARGETS}
    misses = {name: 0 for name, _, _ in TARGETS}
    # Starts EMPTY, not "assumed fine": a container that is already down
    # when this starts should be announced, unlike a TCP target where a
    # restart of this service must not fake a recovery.
    extra_state = set()
    last_digest = ""

    names = ", ".join(f"{n} ({h}:{p})" for n, h, p in TARGETS)
    print(f"watchdog: watching {names} every {INTERVAL}s", flush=True)

    if ANNOUNCE_START:
        tell(f"Watchdog started. Watching: {', '.join(n for n, _, _ in TARGETS)}.")

    while True:
        for name, host, port in TARGETS:
            up = alive(host, port)

            if up:
                if not state[name]:
                    state[name] = True
                    tell(f"✅ {name} is back. ({host}:{port} answering again)")
                    print(f"watchdog: {name} recovered", flush=True)
                misses[name] = 0
                continue

            misses[name] += 1
            if state[name] and misses[name] >= FAILURES_BEFORE_DOWN:
                state[name] = False
                tell(
                    f"🔴 {name} is not answering.\n\n"
                    f"{host}:{port} failed {misses[name]} checks in a row.\n"
                    f"If this is the NUC, the site is down from outside the "
                    f"house as well — it carries all the internet traffic."
                )
                print(f"watchdog: {name} DOWN", flush=True)

        # --- containers and backups, on the same change-only rule -------
        # These are checked here rather than in their own loop so there is
        # exactly one place that decides what "a problem" is, and exactly
        # one alert channel with one debounce.
        found = {}
        found.update(containers())
        found.update(backup_age())
        now_bad = set(found)
        started = now_bad - extra_state
        cleared = extra_state - now_bad
        if started:
            tell("\U0001f534 Something broke:\n"
                 + "\n".join("  - %s %s" % (k, found[k]) for k in sorted(started)))
        if cleared:
            tell("\u2705 Back to normal:\n"
                 + "\n".join("  - " + k for k in sorted(cleared)))
        extra_state = now_bad

        # One digest a day, which is where standing problems get repeated.
        # Repeating them every cycle is how a person learns to ignore the
        # channel, and then misses the one that matters.
        today = time.strftime("%Y-%m-%d")
        if int(time.strftime("%H")) >= DIGEST_HOUR and last_digest != today:
            tell(digest(found))
            last_digest = today

        beat()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
