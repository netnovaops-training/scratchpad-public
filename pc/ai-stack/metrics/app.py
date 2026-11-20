"""
Record the machine into SQLite every two minutes. Keep one month. Hand it
back as text, JSON or a PNG.

WHY THIS EXISTS RATHER THAN A DASHBOARD
=======================================
Netdata already collects all of this beautifully -- but it keeps it in its own
ring buffer, shows it in its own UI, and that UI is the part that didn't work
for you. This service is the boring half: it asks Netdata for the numbers on a
timer and writes them into a plain table you own.

The result is that "what was the GPU doing at 3am last Tuesday" is a SQL query
against a file you can copy, back up, or open in any SQLite browser -- and it
renders on both the CLI and your phone without a browser at all.

WHERE THE NUMBERS COME FROM
---------------------------
  Netdata      GPU, per-container CPU/memory, container counts, the Linux VM's
               CPU/RAM/network -- one HTTP call gets all of it.
  Docker API   container names and states, for "what was down and when".
               Netdata reports how MANY are stopped, never WHICH.
  Windows      CPU %, RAM %, C: free space, real NIC throughput and internet
               latency, from hostmetrics.ps1. None of these are visible from
               inside a container, so when the file is fresh it WINS over
               Netdata's view -- you care about your PC, not about the VM
               Docker runs inside it.

ITS OWN DATABASE, ONE MONTH
---------------------------
This is a separate file on its own volume (metrics_data), nothing else writes
to it, and every insert prunes anything older than 30 days. Worst case one
month of samples is roughly 22k rows -- about 3 MB. It cannot grow without
bound and it cannot take anything else down with it if it is deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import time
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path

import httpx
import matplotlib

matplotlib.use("Agg")           # no display in a container; must precede pyplot
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from shared.history import (
    METRICS,
    format_history_text,
    format_overview_text,
    outages,
    resolve,
)
from shared.printing import printer_problems
from shared.report import format_daily_report
from shared.stats import (
    SECTIONS,
    format_section,
    format_stats_full,
    format_stats_simple,
)
from shared.usage import summarise_llm

DB_PATH = os.environ.get("METRICS_DB", "/data/history.db")
INTERVAL = int(os.environ.get("METRICS_INTERVAL_SECONDS", "120"))
HOST_FILE = os.environ.get("HOST_METRICS", "/host-metrics/host-metrics.json")

# The NUC's numbers, POSTed by enginestats.sh (nuc-homelab) every minute to
# /engine/ingest and parked in the writable data volume. Empty key = ingest
# disabled -- the endpoint refuses everything.
NUC_FILE = os.environ.get("NUC_METRICS", "/data/nuc-metrics.json")
INGEST_KEY = os.environ.get("ENGINE_INGEST_KEY", "")
NETDATA = os.environ.get("NETDATA_URL", "http://netdata:19999")
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
PROJECT = os.environ.get("COMPOSE_PROJECT", "ai-stack")
LITELLM = os.environ.get("LITELLM_BASE", "http://litellm:4000")
OLLAMA = os.environ.get("OLLAMA_BASE", "http://ollama:11434")
ROUTER_KEY = os.environ.get("ROUTER_API_KEY", "")
PRINTER_STATUS = os.environ.get("PRINTER_STATUS",
                                "/host-metrics/printer-status.json")

# One month, as asked. 30 days at one sample per 2 minutes is ~21,600 rows.
RETAIN_DAYS = int(os.environ.get("METRICS_RETAIN_DAYS", "30"))
# Windows metrics older than this are history, not current -- don't let a
# stopped collector freeze the chart on its last reading forever.
HOST_FRESH_SECONDS = 15 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts               INTEGER PRIMARY KEY,   -- unix seconds
    cpu_pct          REAL,
    ram_pct          REAL,
    disk_pct         REAL,
    gpu_util         REAL,
    gpu_vram_pct     REAL,
    gpu_vram_mb      REAL,
    gpu_temp         REAL,
    gpu_power        REAL,
    net_rx_mbps      REAL,
    net_tx_mbps      REAL,
    latency_ms       REAL,
    containers_total INTEGER,
    containers_down  INTEGER,
    down_names       TEXT,
    src              TEXT,                  -- 'windows' or 'vm'
    host_age_min     REAL                   -- how stale hostmetrics.ps1's file was
);
CREATE TABLE IF NOT EXISTS container_samples (
    ts       INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    cpu_pct  REAL,
    mem_mb   REAL,
    PRIMARY KEY (ts, name)
);
CREATE INDEX IF NOT EXISTS idx_cs_name_ts ON container_samples(name, ts);
"""

COLUMNS = ["ts", "cpu_pct", "ram_pct", "disk_pct", "gpu_util", "gpu_vram_pct",
           "gpu_vram_mb", "gpu_temp", "gpu_power", "net_rx_mbps",
           "net_tx_mbps", "latency_ms", "containers_total", "containers_down",
           "down_names", "src", "host_age_min"]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL: readers never block on the writer. Without it, a /history request
    # landing mid-insert gets "database is locked" instead of an answer.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create the tables, then add any column this build knows about.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so adding a column would otherwise mean "delete the volume and lose your
    history" every time this file grows a field. ALTER TABLE ADD COLUMN is
    instant in SQLite and safe to attempt repeatedly.
    """
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(samples)")}
        for col in COLUMNS:
            if col not in have:
                c.execute(f"ALTER TABLE samples ADD COLUMN {col} REAL")
                print(f"[collector] added column {col}", flush=True)
        c.commit()


def num(v) -> float | None:
    try:
        if v is None:
            return None
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
#  Source 1: Netdata
# ---------------------------------------------------------------------------
# /api/v1/allmetrics?format=json returns EVERY chart's current value in one
# response -- roughly 300 KB, once every two minutes. Cheaper and far less
# fragile than one /api/v1/data call per metric, and it means a chart being
# renamed between Netdata versions costs us that one column, not the sample.

def netdata_all(timeout: float = 20.0) -> dict:
    # Netdata was retired 2026-08-31; with the URL unset or not http, skip
    # silently instead of logging an unreachable-host line every cycle for
    # the rest of time. The recorder's real value -- WHICH container was
    # down, and the Windows host transcripts -- never came from Netdata.
    if not NETDATA.startswith("http"):
        return {}
    try:
        r = httpx.get(f"{NETDATA}/api/v1/allmetrics", params={"format": "json"},
                      timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[collector] netdata unreachable: {e}", flush=True)
        return {}


def dims(all_metrics: dict, chart: str) -> dict[str, float]:
    """{dimension name: value} for one chart, or {} if it isn't there."""
    c = all_metrics.get(chart) or {}
    out = {}
    for name, d in (c.get("dimensions") or {}).items():
        v = num(d.get("value"))
        if v is not None:
            out[d.get("name") or name] = v
    return out


def find_charts(all_metrics: dict, prefix: str, contains: str = "") -> list[str]:
    """Chart ids matching a shape, because exact ids differ between versions.

    Netdata's NVIDIA charts are `nvidia_smi.gpu0_gpu_utilization` on one
    release and `nvidia_smi.gpu_utilization` on another. Matching on shape
    means a Netdata upgrade doesn't silently blank the GPU columns.
    """
    return [k for k in all_metrics
            if k.startswith(prefix) and (not contains or contains in k)]


def from_netdata(am: dict, out: dict) -> list[tuple]:
    """Fill GPU, VM-level CPU/RAM/net, and per-container rows. Returns the rows."""
    # --- CPU: Netdata reports time spent in each state; busy is everything
    #     that isn't idle or iowait-waiting.
    cpu = dims(am, "system.cpu")
    if cpu:
        out["cpu_pct"] = round(max(0.0, 100.0 - cpu.get("idle", 0.0)
                                   - cpu.get("iowait", 0.0)), 1)

    # --- RAM: units are MiB. `cached` is memory Linux will hand back the
    #     instant anything asks, so counting it as used reads as a permanent
    #     90% and means nothing.
    ram = dims(am, "system.ram")
    if ram:
        total = sum(ram.values())
        used = ram.get("used", 0.0)
        if total > 0:
            out["ram_pct"] = round(100.0 * used / total, 1)

    # --- disk: the root filesystem of the Docker VM. Overwritten below by
    #     the real C: figure when the Windows collector is running.
    ds = dims(am, "disk_space./")
    if ds:
        total = sum(ds.values())
        if total > 0:
            out["disk_pct"] = round(100.0 * (total - ds.get("avail", 0.0)) / total, 1)

    # --- GPU ---
    for cid in find_charts(am, "nvidia_smi.", "utilization"):
        d = dims(am, cid)
        if d and "memory" not in cid:
            out["gpu_util"] = round(next(iter(d.values())), 1)
            break
    for cid in find_charts(am, "nvidia_smi.", "temperature"):
        d = dims(am, cid)
        if d:
            out["gpu_temp"] = round(next(iter(d.values())), 1)
            break
    for cid in find_charts(am, "nvidia_smi.", "power"):
        d = dims(am, cid)
        if d:
            out["gpu_power"] = round(next(iter(d.values())), 1)
            break
    for cid in find_charts(am, "nvidia_smi.", "frame_buffer_memory"):
        d = dims(am, cid)
        if d:
            used = d.get("used", 0.0)
            total = sum(d.values())     # used + free + reserved
            out["gpu_vram_mb"] = round(used, 1)
            if total > 0:
                out["gpu_vram_pct"] = round(100.0 * used / total, 1)
            break

    # --- network: every real interface added up, in Mbps. Netdata reports
    #     kilobits/s, and `received` is negative on some builds.
    rx = tx = 0.0
    seen_net = False
    for cid in find_charts(am, "net."):
        iface = cid.split(".", 1)[1]
        if iface.startswith(("lo", "veth", "br-", "docker")):
            continue
        d = dims(am, cid)
        if not d:
            continue
        seen_net = True
        rx += abs(d.get("received", 0.0)) / 1000.0
        tx += abs(d.get("sent", 0.0)) / 1000.0
    if seen_net:
        out["net_rx_mbps"] = round(rx, 2)
        out["net_tx_mbps"] = round(tx, 2)

    # --- per container ---
    per: dict[str, dict] = {}
    for cid in find_charts(am, "cgroup_"):
        rest = cid.split(".", 1)
        if len(rest) != 2:
            continue
        name, chart = rest[0][len("cgroup_"):], rest[1]
        d = dims(am, cid)
        if not d:
            continue
        slot = per.setdefault(name, {})
        if chart == "cpu":
            slot["cpu"] = round(sum(d.values()), 2)
        elif chart in ("mem_usage", "mem_usage_limit"):
            slot["mem"] = round(d.get("ram", d.get("used", 0.0)), 1)
    return [(n, v.get("cpu"), v.get("mem")) for n, v in per.items()]


# ---------------------------------------------------------------------------
#  Source 2: the Docker API, for names
# ---------------------------------------------------------------------------
# Talked to over its unix socket with the stdlib rather than the docker SDK or
# the docker CLI -- one small function, no extra package in the image, and no
# shelling out. The socket is mounted READ-ONLY and only ever GET.

def docker_get(path: str, timeout: float = 15.0):
    class _UnixHTTP(HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(DOCKER_SOCK)
            self.sock = s

    conn = _UnixHTTP("localhost", timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"docker {resp.status}: {body[:200]!r}")
        return json.loads(body)
    finally:
        conn.close()


def from_docker(out: dict) -> None:
    """Which containers exist and which are actually broken.

    'down' means broken, not merely stopped: ollama_init exits 0 by design
    once it has pulled the models, and counting that would paint every single
    sample as an outage -- which is the same as having no outage detection.
    """
    try:
        containers = docker_get("/v1.41/containers/json?all=1")
    except Exception as e:  # noqa: BLE001
        print(f"[collector] docker api: {e}", flush=True)
        return

    total = down = 0
    names: list[str] = []
    for c in containers:
        labels = c.get("Labels") or {}
        if labels.get("com.docker.compose.project") != PROJECT:
            continue
        total += 1
        state = (c.get("State") or "").lower()
        status = c.get("Status") or ""
        if state == "running":
            continue
        if state == "exited" and status.startswith("Exited (0)"):
            continue
        down += 1
        nm = (c.get("Names") or ["?"])[0].lstrip("/")
        names.append(nm)

    out["containers_total"] = total
    out["containers_down"] = down
    out["down_names"] = ",".join(sorted(names))


# ---------------------------------------------------------------------------
#  Source 3: Windows
# ---------------------------------------------------------------------------

def from_windows(out: dict) -> None:
    """Overwrite with the real machine's numbers when they're fresh.

    Deliberately last, and deliberately overwriting: Netdata's `system.cpu` is
    the Linux VM Docker Desktop runs, which is a slice of your PC, not your
    PC. When hostmetrics.ps1 is running these are the numbers you'd see in
    Task Manager, which is what you actually mean by "how busy is it".
    """
    try:
        p = Path(HOST_FILE)
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        age = time.time() - p.stat().st_mtime
    except Exception:  # noqa: BLE001
        # No file at all. Recorded as such rather than silently skipped, so
        # /trends can say "the Windows collector has never run" instead of
        # just quietly having no disk or latency columns.
        out["host_age_min"] = None
        return

    # Age is recorded even when the file is too old to use. That number is
    # what turns "the disk chart stopped four days ago" from a mystery into
    # one line telling you the scheduled task died.
    out["host_age_min"] = round(age / 60.0, 1)
    if age > HOST_FRESH_SECONDS:
        return

    for key, col in (("cpu_pct", "cpu_pct"), ("ram_pct", "ram_pct")):
        v = num(raw.get(key))
        if v is not None:
            out[col] = v

    drives = raw.get("drives") or []
    if drives:
        # The fullest drive is the one worth recording -- it's the one that
        # will stop the machine working.
        vals = [num(d.get("pct_used")) for d in drives]
        vals = [v for v in vals if v is not None]
        if vals:
            out["disk_pct"] = max(vals)

    nets = [n for n in (raw.get("net") or []) if n.get("rx_mbps") is not None]
    if nets:
        out["net_rx_mbps"] = round(sum(num(n["rx_mbps"]) or 0 for n in nets), 2)
        out["net_tx_mbps"] = round(sum(num(n.get("tx_mbps")) or 0 for n in nets), 2)

    lat = [v for v in (raw.get("latency_ms") or {}).values()
           if isinstance(v, (int, float))]
    if lat:
        out["latency_ms"] = round(min(lat), 1)

    out["src"] = "windows"


# ---------------------------------------------------------------------------
#  Collect / store
# ---------------------------------------------------------------------------

def collect() -> tuple[dict, list[tuple]]:
    out: dict = {"ts": int(time.time()), "src": "vm"}
    per = from_netdata(netdata_all(), out)
    from_docker(out)
    from_windows(out)
    return out, per


def store(sample: dict, per: list[tuple]) -> None:
    with closing(db()) as c:
        c.execute(
            f"INSERT OR REPLACE INTO samples ({','.join(COLUMNS)}) "
            f"VALUES ({','.join('?' * len(COLUMNS))})",
            [sample.get(k) for k in COLUMNS])
        if per:
            c.executemany(
                "INSERT OR REPLACE INTO container_samples "
                "(ts,name,cpu_pct,mem_mb) VALUES (?,?,?,?)",
                [(sample["ts"], n, cpu, mem) for n, cpu, mem in per])
        # Prune on every write rather than on a second timer: one fewer moving
        # part, and it means the month-long limit holds even if the service is
        # only ever up for a minute at a time.
        #
        # Measured from NOW, not from this sample's timestamp. Those are the
        # same thing for a live sample and wildly different for a backfilled
        # one -- and getting it the other way round means inserting an old row
        # either fails to prune anything or deletes the entire table.
        cutoff = int(time.time()) - RETAIN_DAYS * 86400
        c.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM container_samples WHERE ts < ?", (cutoff,))
        c.commit()


async def loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            store(*collect())
        except Exception as e:  # noqa: BLE001
            print(f"[collector] sample failed: {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------------------
#  API
# ---------------------------------------------------------------------------
app = FastAPI(title="ai-stack history")


@app.on_event("startup")
async def _start() -> None:
    init_db()
    # One sample straight away, so a fresh install has a data point instead of
    # an empty chart for the first two minutes.
    try:
        store(*collect())
    except Exception as e:  # noqa: BLE001
        print(f"[collector] first sample failed: {type(e).__name__}: {e}", flush=True)
    asyncio.create_task(loop())


def _rows(hours: int) -> list[dict]:
    since = int(time.time()) - hours * 3600
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (since,))]


# --- the Engine Bay (2026-08-31): tachometer dials for the host --------------
# Jason asked for "a taco meter" instead of line graphs, and the needle knows
# what he meant. The page is a single static file of canvas dials; this JSON
# is the host-metrics file plus a freshness verdict, because a gauge showing
# stale numbers with confidence is worse than no gauge at all.

@app.get("/engine", response_class=Response)
def engine_page() -> Response:
    with open(os.path.join(os.path.dirname(__file__), "engine.html"),
              encoding="utf-8") as f:
        return Response(f.read(), media_type="text/html; charset=utf-8")


def _machine(path: str, fresh_seconds: int) -> dict:
    import datetime
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        # 2026-11-20: the exception text used to go to the browser; it is
        # logged here instead and the page gets a generic word.
        logging.getLogger("metrics").exception("machine file unreadable: %s", path)
        return {"stale": True, "error": "unreadable"}
    try:
        ts = datetime.datetime.fromisoformat(d.get("ts", ""))
        age = (datetime.datetime.now(ts.tzinfo) - ts).total_seconds()
    except Exception:  # noqa: BLE001
        age = 10 ** 6
    d["age_seconds"] = int(age)
    d["stale"] = age > fresh_seconds
    return d


@app.get("/engine/data")
def engine_data() -> dict:
    # SuperNova writes every 2 minutes (3 missed beats = stale); the NUC
    # posts every minute (same grace, scaled).
    sn = _machine(HOST_FILE, 420)
    nuc = _machine(NUC_FILE, 240)
    out = dict(sn)          # old shape on top, so nothing that read the
    out["machines"] = {     # single-machine form breaks
        "supernova": sn,
        "nuc": nuc,
    }
    return out


@app.get("/engine/prom", response_class=PlainTextResponse)
def engine_prom() -> str:
    """The same numbers as /engine/data, in Prometheus text format (2026-11-04).

    Added so the NUC's Prometheus keeps 90 days of GPU, VRAM and drive history:
    the crash review that night could not trace VRAM because nothing recorded
    it. Read-only; only numbers, never names of files or processes. Scraped by
    the NUC (job "engine") and drawn on Jason's Debug Found dashboard.
    """
    lines = []

    def put(name, value, **labels):
        v = num(value)
        if v is None:
            return
        lab = ",".join(f'{k}="{str(val).replace(chr(34), "")}"' for k, val in labels.items())
        lines.append(f"engine_{name}{{{lab}}} {v}")

    data = engine_data()
    for machine, m in (data.get("machines") or {}).items():
        if not isinstance(m, dict):
            continue
        put("cpu_pct", m.get("cpu_pct"), machine=machine)
        put("ram_pct", m.get("ram_pct"), machine=machine)
        put("cpu_temp_c", m.get("cpu_temp_c"), machine=machine)
        g = m.get("gpu") or {}
        if isinstance(g, dict):
            put("gpu_util_pct", g.get("util_pct"), machine=machine)
            put("gpu_temp_c", g.get("temp_c"), machine=machine)
            put("gpu_vram_used_mb", g.get("vram_used_mb"), machine=machine)
            put("gpu_vram_total_mb", g.get("vram_total_mb"), machine=machine)
        for d in m.get("drives") or []:
            if isinstance(d, dict):
                put("drive_used_pct", d.get("pct_used"), machine=machine, drive=d.get("name", "?"))
                put("drive_free_gb", d.get("free_gb"), machine=machine, drive=d.get("name", "?"))
    return "\n".join(lines) + "\n"


@app.post("/engine/ingest")
async def engine_ingest(request: Request) -> dict:
    """The NUC's heartbeat. Shared secret, meant for the LAN port only --
    the internet never reaches this container (router forwards 80/443 to
    HAProxy/Caddy, and Caddy's /engine route is GET-proxied UI, not this)."""
    if not INGEST_KEY:
        raise HTTPException(status_code=403, detail="ingest disabled")
    if request.headers.get("x-engine-key") != INGEST_KEY:
        raise HTTPException(status_code=403, detail="bad key")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="want a JSON object")
    tmp = NUC_FILE + ".tmp"

    def write():
        """Write the sample and swap it in (a thread: file I/O must not block the event loop, ASYNC230)."""
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(body, f)
        os.replace(tmp, NUC_FILE)

    await asyncio.to_thread(write)
    return {"ok": True}


@app.get("/healthz")
def healthz() -> dict:
    with closing(db()) as c:
        row = c.execute(
            "SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM samples"
        ).fetchone()
    last = row["last"]
    return {
        "ok": True,
        "samples": row["n"],
        "oldest": row["first"],
        "newest": last,
        "age_seconds": (int(time.time()) - last) if last else None,
        "interval_seconds": INTERVAL,
        "retain_days": RETAIN_DAYS,
    }


@app.get("/history.txt", response_class=PlainTextResponse)
def history_txt(hours: int = 24, metric: str = "") -> str:
    """The CLI's view. Rendered here, not in the CLI, so both surfaces agree."""
    rows = _rows(hours)
    if not metric:
        return format_overview_text(rows, hours)
    key = resolve(metric)
    if not key:
        return ("Don't know that one. Try: "
                + ", ".join(METRICS) + "\n  /history            (everything)"
                + "\n  /history vram 48    (one metric, 48 hours)")
    return format_history_text(rows, key, hours)


def _assemble(hours: int = 24) -> dict:
    """Everything /stats can show, gathered once.

    Each source is wrapped on its own: a stopped Ollama costs you the
    "loaded models" line and nothing else. A stats screen that returns an
    error because one of eight sources is down is useless precisely when you
    are trying to find out which one.
    """
    rows = _rows(hours)
    cur = rows[-1] if rows else {}
    d: dict = {
        "rows": rows,
        "current": cur,
        "src": cur.get("src"),
        "containers_total": cur.get("containers_total"),
        "containers_down": cur.get("containers_down"),
        "down_names": cur.get("down_names"),
        "problems": [],
    }

    # --- the Windows side -------------------------------------------------
    try:
        raw = json.loads(Path(HOST_FILE).read_text(encoding="utf-8-sig"))
        age = (time.time() - Path(HOST_FILE).stat().st_mtime) / 60.0
        d["host"] = raw
        if age > 15:
            d["problems"].append(
                f"Windows metrics are {int(age)} min old "
                "- hostmetrics.cmd -Status")
    except FileNotFoundError:
        d["host"] = {}
        d["problems"].append(
            "no Windows metrics - run hostmetrics.cmd -Install "
            "(disk, real network and latency are blank without it)")
    except Exception as e:  # noqa: BLE001
        d["host"] = {}
        d["problems"].append(f"Windows metrics unreadable: {type(e).__name__}")

    # --- per container ----------------------------------------------------
    try:
        with closing(db()) as c:
            last = c.execute(
                "SELECT MAX(ts) t FROM container_samples").fetchone()["t"]
            if last:
                d["per_container"] = [dict(r) for r in c.execute(
                    "SELECT name, cpu_pct, mem_mb FROM container_samples "
                    "WHERE ts = ? ORDER BY name", (last,))]
    except Exception as e:  # noqa: BLE001
        print(f"[stats] per-container: {e}", flush=True)

    # --- netdata: the Docker VM's own memory ------------------------------
    am = netdata_all(timeout=10.0)
    ram = dims(am, "system.ram")
    if ram:
        total = sum(ram.values())
        if total:
            d["vm"] = {"pct": round(100 * ram.get("used", 0) / total),
                       "used_mb": int(ram.get("used", 0)),
                       "total_mb": int(total)}

    # --- docker's own disk usage ------------------------------------------
    try:
        df = docker_get("/v1.41/system/df")
        d["docker_disk"] = [
            {"type": "Images",
             "size": _mb(sum(i.get("Size") or 0 for i in df.get("Images") or [])),
             "reclaimable": _mb(sum(i.get("Size") or 0
                                    for i in df.get("Images") or []
                                    if not i.get("Containers"))) },
            {"type": "Volumes",
             "size": _mb(sum((v.get("UsageData") or {}).get("Size") or 0
                             for v in df.get("Volumes") or [])),
             "reclaimable": "-"},
        ]
    except Exception as e:  # noqa: BLE001
        print(f"[stats] docker df: {e}", flush=True)

    # --- models -----------------------------------------------------------
    try:
        r = httpx.get(f"{LITELLM}/metrics", timeout=15.0,
                      headers={"Authorization": f"Bearer {ROUTER_KEY}"},
                      follow_redirects=True)
        if r.status_code == 200:
            d["llm"] = summarise_llm(r.text)
    except Exception as e:  # noqa: BLE001
        print(f"[stats] litellm: {e}", flush=True)

    try:
        routes = httpx.get(f"{LITELLM}/model/info", timeout=15.0,
                           headers={"Authorization": f"Bearer {ROUTER_KEY}"},
                           follow_redirects=True).json()
        installed = httpx.get(f"{OLLAMA}/api/tags", timeout=15.0).json()
        have = {m.get("name", "") for m in installed.get("models") or []}
        have |= {n.split(":")[0] for n in have}
        out = []
        for m in routes.get("data") or []:
            name = ((m.get("litellm_params") or {}).get("model") or "")
            short = name.split("/", 1)[-1]
            out.append({"route": m.get("model_name"), "model": short,
                        "installed": short in have
                        or f"{short}:latest" in have
                        or short.split(":")[0] in have})
        d["routes"] = out
        for x in out:
            if not x["installed"]:
                d["problems"].append(
                    f"route '{x['route']}' points at {x['model']}, "
                    "which is not installed")
    except Exception as e:  # noqa: BLE001
        print(f"[stats] routes: {e}", flush=True)

    try:
        ps = httpx.get(f"{OLLAMA}/api/ps", timeout=10.0).json()
        d["loaded_models"] = [
            {"name": m.get("name"), "size_mb": round((m.get("size") or 0) / 1e6)}
            for m in ps.get("models") or []]
    except Exception as e:  # noqa: BLE001
        print(f"[stats] ollama ps: {e}", flush=True)

    # --- printer ----------------------------------------------------------
    try:
        raw = json.loads(Path(PRINTER_STATUS).read_text(encoding="utf-8-sig"))
        today = [j for j in (raw.get("recent") or [])
                 if (j.get("time") or "")[:10] >= _date_str(time.time() - hours * 3600)]
        raw["today"] = today
        raw["age_min"] = round(
            (time.time() - Path(PRINTER_STATUS).stat().st_mtime) / 60.0, 1)
        d["printer"] = raw
        # A stale file is its own problem, and a more important one than
        # anything it contains: every reading in it is history.
        if raw["age_min"] > 5:
            d["problems"].append(
                f"printer status is {int(raw['age_min'])} min old "
                "- the print server isn't running (printserver.cmd -Install)")
            if raw.get("queue_pending"):
                d["problems"].append(
                    f"{raw['queue_pending']} file(s) queued but nothing is printing")
        else:
            d["problems"].extend(printer_problems(raw))
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[stats] printer: {e}", flush=True)

    if d.get("containers_down"):
        d["problems"].insert(0, f"DOWN: {d.get('down_names')}")
    return d


def _mb(n: int) -> str:
    return f"{n / 1e9:.2f}GB" if n >= 1e9 else f"{n / 1e6:.0f}MB"


@app.get("/stats.txt", response_class=PlainTextResponse)
def stats_txt(mode: str = "simple", section: str = "", page: int = 0,
              hours: int = 24) -> str:
    """One command, two depths, rendered once for both surfaces.

    mode=simple   the glance screen
    mode=full     everything; `page` picks one of the pages
    section=gpu   just that section
    """
    d = _assemble(hours)
    if section:
        return format_section(d, section)
    if mode == "full":
        pages = format_stats_full(d)
        if not pages:
            return "no data yet"
        return pages[max(0, min(page, len(pages) - 1))]
    return format_stats_simple(d)


@app.get("/stats.json")
def stats_json(hours: int = 24) -> JSONResponse:
    """The same data as /stats.txt, unformatted, for n8n or anything else."""
    d = _assemble(hours)
    d.pop("rows", None)          # far too big for a status payload
    return JSONResponse(d)


@app.get("/stats.pages")
def stats_pages(hours: int = 24) -> JSONResponse:
    """How many pages `mode=full` will produce, so a caller can loop."""
    return JSONResponse({"pages": len(format_stats_full(_assemble(hours))),
                         "sections": list(SECTIONS)})


@app.get("/report.txt", response_class=PlainTextResponse)
def report_txt(hours: int = 24) -> str:
    """The 8pm digest, rendered here so n8n needs no logic at all.

    Everything the report needs is fetched in this one place: the recorded
    history from our own table, model usage straight from LiteLLM, and the
    printer status from the Windows watcher's file. Each is optional -- a
    missing piece costs that section, never the whole report, because a
    nightly summary that silently stops arriving is worse than one with a
    gap in it.
    """
    rows = _rows(hours)

    # --- model usage, straight from LiteLLM -------------------------------
    llm: dict = {}
    try:
        r = httpx.get(f"{LITELLM}/metrics", timeout=15.0,
                      headers={"Authorization": f"Bearer {ROUTER_KEY}"},
                      # LiteLLM redirects /metrics to /metrics/, and httpx
                      # does not follow redirects unless told to. Leaving
                      # this off cost a long debugging session once already.
                      follow_redirects=True)
        if r.status_code == 200:
            llm = summarise_llm(r.text)
    except Exception as e:  # noqa: BLE001
        print(f"[report] litellm: {e}", flush=True)

    # --- printer ----------------------------------------------------------
    printer: dict | None = None
    try:
        raw = json.loads(Path(PRINTER_STATUS).read_text(encoding="utf-8-sig"))
        cutoff = time.time() - hours * 3600
        today = []
        for j in raw.get("recent") or []:
            t = j.get("time") or ""
            # String compare against an ISO date is enough here and cannot
            # throw on an unexpected offset format.
            if t[:10] >= _date_str(cutoff):
                today.append(j)
        printer = {
            "today": today,
            "supplies": raw.get("supplies") or [],
            "problems": printer_problems(raw),
        }
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[report] printer: {e}", flush=True)

    return format_daily_report(rows, llm, printer, hours=hours)


def _date_str(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


@app.get("/history.json")
def history_json(hours: int = 24, metric: str = "") -> JSONResponse:
    rows = _rows(hours)
    key = resolve(metric) if metric else None
    if key:
        col = METRICS[key][0]
        rows = [{"ts": r["ts"], "v": r[col]} for r in rows]
    return JSONResponse({"hours": hours, "metric": key, "count": len(rows),
                         "outages": outages(_rows(hours)) if not key else None,
                         "rows": rows})


@app.get("/containers.json")
def containers_json(hours: int = 24, name: str = "") -> JSONResponse:
    since = int(time.time()) - hours * 3600
    q = "SELECT ts,name,cpu_pct,mem_mb FROM container_samples WHERE ts >= ?"
    args: list = [since]
    if name:
        q += " AND name = ?"
        args.append(name)
    with closing(db()) as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY ts", args)]
    return JSONResponse({"count": len(rows), "rows": rows})


@app.get("/history.png")
def history_png(hours: int = 24, metric: str = "cpu") -> Response:
    """One metric as a line chart. Deliberately plain: readable on a phone."""
    key = resolve(metric) or "cpu"
    col, label, unit, is_pct = METRICS[key]
    rows = _rows(hours)

    pts = [(r["ts"], r[col]) for r in rows if r.get(col) is not None]
    xs = [mdates.date2num(_dt(t)) for t, _ in pts]
    ys = [v for _, v in pts]

    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=110)
    if not xs:
        ax.text(0.5, 0.5, f"No {label} data yet\n(sampling every "
                          f"{INTERVAL // 60} min, keeping {RETAIN_DAYS} days)",
                ha="center", va="center", fontsize=11, color="#666")
        ax.set_axis_off()
    else:
        ax.plot(xs, ys, linewidth=1.6, color="#2a7fdb")
        ax.fill_between(xs, ys, alpha=0.12, color="#2a7fdb")

        # Shade the periods where something was down. More useful than the
        # line itself for "what happened last night" -- a crashed container
        # uses no CPU, so an outage looks like calm on a usage chart.
        for run in outages(rows):
            ax.axvspan(mdates.date2num(_dt(run["start"])),
                       mdates.date2num(_dt(run["end"] + INTERVAL)),
                       color="#e03131", alpha=0.18, lw=0)

        ax.set_ylabel(label + (f" ({unit})" if unit else ""))
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%d %b" if hours > 48 else "%H:%M"))
        fig.autofmt_xdate(rotation=0, ha="center")
        if is_pct:
            ax.set_ylim(0, 100)

    ax.set_title(f"{label} - last {hours}h", fontsize=11)
    fig.tight_layout()

    from io import BytesIO
    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return Response(content=buf.getvalue(), media_type="image/png")


def _dt(ts: int):
    from datetime import datetime
    return datetime.fromtimestamp(ts)
