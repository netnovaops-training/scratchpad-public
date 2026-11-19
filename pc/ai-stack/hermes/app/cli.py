r"""
Hermes CLI - the terminal front end.

Open it with the Desktop shortcut, hermes.cmd, or:
    docker compose exec hermes hermes
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from shared.health import format_health
from shared.history import METRICS as HISTORY_METRICS
from shared.history import resolve as resolve_metric
from shared.identity import by_email
from shared.memory import MemoryStore
from shared.models_health import (
    parse_health,
    parse_installed,
    parse_routes,
    reconcile,
)
from shared.router import Router
from shared.transcript import Transcript
from shared.usage import (
    diagnose_metrics,
    gpu_from_ollama,
    gpu_from_stats,
    summarise_llm,
)
from shared.voice import Voice


def _identity_for_cli() -> str:
    """Whoever is at the keyboard, checked against people.json, or "".

    THE SINGLE ANSWER to "who is this", used for the memory store AND for the
    household index AND for whether the garden is offered. It used to be
    worked out here for memory only, leaving the find_* tools to fall back to
    FIND_AS -- a second variable holding a second address, which is one more
    than a single-user terminal can keep consistent.
    """
    email = os.environ.get("HERMES_USER_EMAIL") or os.environ.get("ADMIN_OWNERS", "")
    email = email.split(",")[0].strip()
    if not email:
        return ""
    person = by_email(email)
    if person is None:
        return ""
    return person.email


def _memory_for_cli() -> MemoryStore | None:
    """The memory store for whoever is running the CLI, or None.

    WHO THE CLI IS. There is no login here -- it is a terminal on Jason's own
    machine -- so the identity comes from HERMES_USER_EMAIL, defaulting to the
    household owner. It is checked against people.json rather than trusted,
    so a typo produces no memory instead of a new folder named after the typo.

    RETURNING None IS A REAL ANSWER. Unidentified means no memory at all,
    never a shared one: a shared store would put one person's facts in front
    of another with no way to tell whose they were.
    """
    root = os.environ.get("HERMES_MEMORY_ROOT")
    if not root:
        return None
    email = os.environ.get("HERMES_USER_EMAIL") or os.environ.get("ADMIN_OWNERS", "")
    email = email.split(",")[0].strip()
    if not email:
        return None
    person = by_email(email)
    if person is None:
        # Say so rather than inventing a folder. A memory that silently
        # attaches to nobody is worse than none.
        print(f"[memory] {email} is not in people.json -- running without memory")
        return None
    try:
        store = MemoryStore(root, person.email, person.display)
        store.ensure()
        return store
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] unavailable ({exc}) -- running without it")
        return None

from . import garden_tools, ui
from .agent import Agent

console = Console()

STATE_DIR = Path("/state")
STATE_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_OUT = Path("/workspace/audio")

# Docker service names, not localhost -- these are OTHER containers on the
# same network. /usage reads LiteLLM directly so token counts survive n8n
# being down; only the GPU numbers need n8n (it alone has the Docker socket
# to run nvidia-smi), and that falls back to asking Ollama.
LITELLM_BASE = "http://litellm:4000"
LITELLM_METRICS_URL = f"{LITELLM_BASE}/metrics"
OLLAMA_TAGS_URL = "http://ollama:11434/api/tags"
N8N_STATS_URL = "http://n8n:5678/webhook/stats"
OLLAMA_PS_URL = "http://ollama:11434/api/ps"

# The recorder. Asked over HTTP rather than by opening its SQLite file
# directly: one writer, no file locking to get wrong, and the same rendering
# code answers here and on Telegram so the two can never disagree.
METRICS_URL = os.environ.get("METRICS_URL", "http://metrics:8000")

# Same key the router already uses. Harmless when /metrics is open, and the
# difference between working and a silent 401 when it isn't.
METRICS_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('ROUTER_API_KEY', '')}"
}

COMMANDS = [
    "/help", "/job", "/jobs", "/auto", "/voice", "/voices", "/speak", "/system",
    "/usage", "/models", "/health", "/history", "/trends", "/stats",
    "/stats full", "/new", "/transcript",
    "/export", "/sh",
    "/confirm", "/clear",
    "/exit", "/quit",
]

BANNER = r"""
  _  _
 | || |___ _ _ _ __  ___ ___
 | __ / -_) '_| '  \/ -_|_-<
 |_||_\___|_| |_|_|_\___/__/
"""


class Hermes:
    def __init__(self) -> None:
        self.router = Router()
        self.job = os.environ.get("DEFAULT_JOB", "chat")
        # Auto-routing ON by default. Picking a model per message was always
        # busywork -- the router already guesses well enough, and when it
        # guesses wrong the cost is "a slightly different model answered",
        # not a failure. Set AUTO_JOB=false in .env to go back to manual.
        self.auto = os.environ.get("AUTO_JOB", "true").lower() == "true"
        self.speak = os.environ.get("HERMES_SPEAK", "false").lower() == "true"
        self.confirm_mutations = True
        self.voice = Voice()
        self.transcript = Transcript(surface="hermes")
        self.agent = Agent(
            router=self.router,
            transcript=self.transcript,
            job=self.job,
            auto_job=self.auto,
            confirm=self._confirm,
            on_tool_start=self._tool_start,
            on_tool=self._show_tool,
            memory=_memory_for_cli(),
            # Search and the garden run as the SAME person the memory belongs
            # to. Empty means unidentified, and then find_* falls back to
            # FIND_AS -- which is now the only thing that constant is for.
            identity_email=_identity_for_cli(),
            garden=garden_tools.allowed_for(_identity_for_cli()),
        )
        self.session = PromptSession(
            history=FileHistory(str(STATE_DIR / "history")),
            completer=WordCompleter(COMMANDS, ignore_case=True),
        )

    # -- ui callbacks ---------------------------------------------------------

    def _confirm(self, name: str, args: dict) -> bool:
        if not self.confirm_mutations:
            return True
        preview = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())
        console.print(f"[yellow]>> {name}[/yellow]([dim]{preview}[/dim])")
        try:
            answer = console.input("[yellow]   allow? [Y/n] [/yellow]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("", "y", "yes")

    def _tool_start(self, name: str, args: dict) -> None:
        ui.tool_start(name)

    def _show_tool(self, name: str, args: dict, result: str, seconds: float = 0.0) -> None:
        # One line per tool, always the same shape. The full output already went
        # to the model and the transcript; reprinting it here just buries the
        # answer you're waiting for.
        ui.tool_done(name, args, result, seconds)

    # -- commands -------------------------------------------------------------

    def cmd_help(self) -> None:
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("/job <name>", "switch job route (coding, reasoning, fast, ...)")
        t.add_row("/jobs", "list routes the router currently offers")
        t.add_row("/system", "containers, CPU/mem, GPU, model usage - right here")
        t.add_row("/usage", "GPU + tokens per model route (works without n8n)")
        t.add_row("/models", "check configured models are installed (free)")
        t.add_row("/health", "GPU, temps, VRAM, disk, RAM, network, uptime, down")
        t.add_row("/stats", "right now, at a glance")
        t.add_row("/stats full", "every detail: containers, disks, NICs, routes, printer")
        t.add_row("/stats <section>", "machine gpu containers disks network models printer history")
        t.add_row("/trends", "the last 24h as sparklines - what's rising, what broke")
        t.add_row("/history", "same thing (/trends is just easier to remember)")
        t.add_row("/history <metric> [h]", "one metric: cpu ram disk gpu vram temp power net ping")
        t.add_row("/models <route>", "really call one route (loads it into VRAM)")
        t.add_row("/auto", "toggle automatic job selection per message")
        t.add_row("/voice <name>", "pick a cloned voice from ./voices")
        t.add_row("/voices", "list available cloned voices")
        t.add_row("/speak", "toggle speaking replies out loud (writes .wav)")
        t.add_row("/confirm", "toggle asking before file writes and shell commands")
        t.add_row("/new", "start a fresh conversation + transcript")
        t.add_row("/transcript", "show where this session's transcript lives")
        t.add_row("/export [md|txt|json]", "export this session")
        t.add_row("/sh <cmd>", "run a shell command directly, no model involved")
        t.add_row("/clear", "clear the screen")
        t.add_row("/exit", "quit")
        console.print(Panel(t, title="commands", border_style="cyan"))

    def cmd_jobs(self) -> None:
        jobs = self.router.available_jobs()
        t = Table("route", "status", box=None)
        for j in jobs:
            t.add_row(j, "[green]active[/green]" if j == self.job else "")
        console.print(Panel(t, title="router jobs", border_style="cyan"))

    def cmd_system(self) -> None:
        """The same snapshot n8n's heartbeat works from, as text.

        Calls n8n directly (http://n8n:5678/webhook/stats) rather than going
        through anything else -- it works even if the docker CLI plumbing
        n8n uses ever changes, and it fails with one clear line rather than a
        stack trace if n8n itself is the thing that's down.
        """
        try:
            r = httpx.get("http://n8n:5678/webhook/stats", timeout=15.0, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]couldn't reach n8n[/red] ({e})")
            console.print("[dim]docker compose ps n8n   on the PC will say why[/dim]")
            return

        t = Table("container", "cpu", "mem", "state", box=None)
        by_name = {s["name"]: s for s in data.get("stats", [])}
        for c in data.get("containers", []):
            s = by_name.get(c["name"], {})
            state_style = "green" if c["state"] == "running" else "red"
            t.add_row(
                c["name"],
                s.get("cpu", "-"),
                s.get("mem_pct", "-"),
                f"[{state_style}]{c['state']}[/{state_style}]",
            )
        console.print(Panel(t, title="containers", border_style="cyan"))

        gpu = data.get("gpu")
        if gpu:
            pct = round(100 * gpu["mem_used_mb"] / gpu["mem_total_mb"]) if gpu["mem_total_mb"] else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            console.print(
                f"  gpu   [cyan]{bar}[/cyan] {pct}% vram"
                f"  ({gpu['mem_used_mb']}MB / {gpu['mem_total_mb']}MB)"
                f"  util {gpu['util_pct']}%  {gpu['temp_c']}°C"
            )
        else:
            console.print("  [dim]gpu: not reported[/dim]")

        down = data.get("down_count", 0)
        if down:
            console.print(f"  [red]{down} container(s) down: {', '.join(data.get('down_names', []))}[/red]")
        else:
            console.print("  [green]all containers running[/green]")

        console.print(f"  [dim]as of {data.get('generated_at', '?')}[/dim]")
        console.print("  [dim]/trends for the last 24h, /health for what's wrong[/dim]")

    def cmd_usage(self) -> None:
        """GPU and per-route token usage, with n8n optional.

        /system needs n8n; this deliberately doesn't for the LLM half. The
        numbers come straight from LiteLLM, and the GPU half degrades to
        asking Ollama what it has loaded rather than printing nothing. Each
        fetch is independent -- one being down costs you that section only.
        """
        metrics_text, stats_json, ps_json = "", None, None
        reached = False
        status: int | None = None
        err = ""

        try:
            # /metrics is usually unauthenticated, but some LiteLLM builds
            # gate it behind a key. Sending the one we already have costs
            # nothing and covers both.
            r = httpx.get(LITELLM_METRICS_URL, timeout=10.0, headers=METRICS_HEADERS, follow_redirects=True)
            # Keep the status. `raise_for_status` throws for 404 exactly as it
            # does for a refused connection, and those need opposite fixes --
            # swallowing both into one bool is what made this say "LiteLLM
            # didn't answer" about a container that was up and healthy.
            status = r.status_code
            r.raise_for_status()
            metrics_text = r.text
            reached = True
        except httpx.HTTPStatusError:
            pass
        except Exception as e:  # noqa: BLE001
            status = None
            err = f"{type(e).__name__}: {e}"

        try:
            r = httpx.get(N8N_STATS_URL, timeout=10.0, follow_redirects=True)
            r.raise_for_status()
            stats_json = r.json()
        except Exception:  # noqa: BLE001, S110  status panel: a down service just shows as unknown
            pass

        gpu = gpu_from_stats(stats_json)
        if gpu is None:
            try:
                r = httpx.get(OLLAMA_PS_URL, timeout=10.0, follow_redirects=True)
                r.raise_for_status()
                ps_json = r.json()
            except Exception:  # noqa: BLE001, S110  status panel: a down service just shows as unknown
                pass
            gpu = gpu_from_ollama(ps_json)

        llm = summarise_llm(metrics_text)

        # GPU panel
        if gpu and gpu["source"] == "nvidia-smi":
            used, total = gpu.get("mem_used_mb"), gpu.get("mem_total_mb")
            pct = round(100 * used / total) if (used is not None and total) else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            extra = "  ".join(
                x for x in (
                    f"util {gpu['util_pct']}%" if gpu.get("util_pct") is not None else "",
                    f"{gpu['temp_c']}°C" if gpu.get("temp_c") is not None else "",
                    f"{gpu['power_w']}W" if gpu.get("power_w") is not None else "",
                ) if x
            )
            console.print(
                f"  gpu   [cyan]{bar}[/cyan] {pct}% vram"
                f"  ({gpu['mem_used_mb']}MB / {gpu['mem_total_mb']}MB)  {extra}"
            )
        elif gpu:
            models = gpu.get("models") or []
            if models:
                console.print(f"  gpu   [cyan]{gpu['mem_used_mb']}MB[/cyan] in VRAM "
                              f"[dim](via ollama; start n8n for util/temp)[/dim]")
                for m in models:
                    console.print(f"          {m['name']}  {m['vram_mb']}MB")
            else:
                console.print("  [dim]gpu: no model currently loaded[/dim]")
        else:
            console.print("[red]  gpu: neither n8n nor ollama answered[/red]")

        # LLM usage table
        if not llm:
            for ln in diagnose_metrics(reached, metrics_text, llm,
                                        status, err).split("\n"):
                console.print(f"  [yellow]{ln}[/yellow]" if not ln.startswith("  ")
                              else f"  [dim]{ln.strip()}[/dim]")
            return

        def n(v: object) -> str:
            return "-" if v is None else f"{round(float(v)):,}"  # type: ignore[arg-type]

        t = Table("route", "requests", "tokens", "in", "out", "avg", box=None)
        for route, d in llm.items():
            t.add_row(
                route,
                n(d.get("requests")),
                n(d.get("tokens_total")),
                n(d.get("tokens_in")),
                n(d.get("tokens_out")),
                f"{d['latency_avg_s']:.1f}s" if d.get("latency_avg_s") is not None else "-",
            )
        console.print(Panel(t, title="llm usage since litellm restarted",
                            border_style="cyan"))

    def cmd_health(self) -> None:
        """The whole picture, problems first."""
        try:
            r = httpx.get(N8N_STATS_URL, timeout=60.0, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]couldn't reach the stats endpoint[/red] ({e})")
            console.print("[dim]docker compose ps n8n[/dim]")
            return
        text = format_health(data)
        for line in text.split("\n"):
            if line.startswith("  ! "):
                console.print(f"[red]{line}[/red]")
            elif line.startswith("All good"):
                console.print(f"[green]{line}[/green]")
            elif line and not line.startswith(" "):
                console.print(f"[cyan]{line}[/cyan]")
            else:
                console.print(line)

    def cmd_stats(self, arg: str = "") -> None:
        """/stats, /stats full, /stats <section>.

        Rendered by the metrics service, same as Telegram, so the two can
        never disagree about what "right now" looks like.
        """
        arg = (arg or "").strip().lower()
        params: dict = {}
        if arg in ("full", "all", "everything"):
            params = {"mode": "full"}
        elif arg:
            params = {"section": arg}
        else:
            params = {"mode": "simple"}

        try:
            if params.get("mode") == "full":
                n = httpx.get(f"{METRICS_URL}/stats.pages", timeout=60.0,
                              follow_redirects=True).json().get("pages", 1)
                out = []
                for i in range(n):
                    r = httpx.get(f"{METRICS_URL}/stats.txt", timeout=60.0,
                                  params={"mode": "full", "page": i},
                                  follow_redirects=True)
                    r.raise_for_status()
                    # Strip the "[1/3]" page markers -- a terminal scrolls, so
                    # paging is Telegram's problem, not ours.
                    out.append(r.text.rsplit("\n\n[", 1)[0] if "\n\n[" in r.text else r.text)
                text = "\n\n".join(out)
            else:
                r = httpx.get(f"{METRICS_URL}/stats.txt", timeout=60.0,
                              params=params, follow_redirects=True)
                r.raise_for_status()
                text = r.text
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]couldn't reach the recorder[/red] ({type(e).__name__}: {e})")
            console.print("[dim]docker compose ps metrics[/dim]")
            return

        for line in text.split("\n"):
            if line.startswith("  ! "):
                console.print(f"[red]{line}[/red]")
            elif line.startswith("All good"):
                console.print(f"[green]{line}[/green]")
            elif line and not line.startswith(" ") and line == line.upper():
                console.print(f"[cyan]{line}[/cyan]")
            elif "MISSING" in line:
                console.print(f"[yellow]{line}[/yellow]")
            else:
                console.print(line)

    def cmd_history(self, arg: str = "") -> None:
        """What the machine has been doing, from the recorder's own table.

            /history            everything, last 24h
            /history vram       one metric
            /history vram 48    one metric, 48 hours
            /history 72         everything, 72 hours

        The text is rendered by the metrics service, not here, so this and
        the Telegram version can't drift apart.
        """
        metric, hours = "", 24
        for tok in arg.split():
            if tok.isdigit():
                hours = max(1, min(24 * 31, int(tok)))   # a month is all we keep
            elif not metric:
                metric = tok
        if metric and not resolve_metric(metric):
            console.print(f"[yellow]don't chart '{metric}'[/yellow] - try: "
                          + ", ".join(HISTORY_METRICS))
            return

        try:
            r = httpx.get(f"{METRICS_URL}/history.txt",
                          params={"hours": hours, "metric": metric}, timeout=30.0, follow_redirects=True)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            # Named separately from the other services on purpose: "the
            # recorder is down" and "there's no history yet" have completely
            # different fixes, and one vague message sends you to the wrong one.
            console.print(f"[red]couldn't reach the recorder[/red] ({type(e).__name__}: {e})")
            console.print("[dim]docker compose ps metrics[/dim]")
            return

        for line in r.text.split("\n"):
            if line.startswith(("  ", "    ")) and "outage" not in line:
                console.print(line)
            elif "outage" in line.lower():
                console.print(f"[red]{line}[/red]")
            elif line and not line.startswith(" ") and not any(
                    ch in line for ch in "▁▂▃▄▅▆▇█"):
                console.print(f"[cyan]{line}[/cyan]")
            else:
                console.print(line)

    def cmd_models(self, arg: str = "") -> None:
        """Free check by default; a real call only when you name a route.

        `/models` loads nothing -- it just compares what LiteLLM has
        configured against what Ollama has on disk. `/models <route>` sends
        a real request, which pulls that model into VRAM, so it's one at a
        time and never automatic.
        """
        if arg:
            route = arg.strip().split()[0]
            console.print(f"[dim]calling {route} for real - this loads it into VRAM...[/dim]")
            try:
                r = httpx.get(f"{LITELLM_BASE}/health", params={"model": route},
                              headers=METRICS_HEADERS, timeout=120.0, follow_redirects=True)
                r.raise_for_status()
                res = parse_health(r.json(), route)
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]couldn't check {route}[/red] ({e})")
                return
            if res["live"] is True:
                console.print(f"  [green]{route}: working[/green]")
            elif res["live"] is False:
                console.print(f"  [red]{route}: FAILING[/red] {res['error']}")
            else:
                console.print(f"  [yellow]{route}: no clear answer[/yellow] ({res['error']})")
            return

        mi, tags = None, None
        try:
            r = httpx.get(f"{LITELLM_BASE}/model/info", headers=METRICS_HEADERS, timeout=10.0, follow_redirects=True)
            r.raise_for_status()
            mi = r.json()
        except Exception:  # noqa: BLE001, S110  status panel: a down service just shows as unknown
            pass
        try:
            r = httpx.get(OLLAMA_TAGS_URL, timeout=10.0, follow_redirects=True)
            r.raise_for_status()
            tags = r.json()
        except Exception:  # noqa: BLE001, S110  status panel: a down service just shows as unknown
            pass

        installed = parse_installed(tags)
        rows = reconcile(parse_routes(mi), installed)
        if not rows:
            console.print("[red]  no routes found - is litellm up?[/red]")
            return

        t = Table("route", "model", "on disk", box=None)
        for r_ in rows:
            if r_["installed"] is True:
                mark = "[green]yes[/green]"
            elif r_["installed"] is False:
                mark = "[red]MISSING[/red]"
            else:
                mark = "[dim]cloud[/dim]"
            t.add_row(r_["route"], r_["backing"] or "?", mark)
        console.print(Panel(t, title="model routes", border_style="cyan"))

        for r_ in rows:
            if r_["installed"] is False:
                console.print(f"  [yellow]docker exec ollama ollama pull {r_['backing']}[/yellow]")

    def cmd_voices(self) -> None:
        if not self.voice.available():
            console.print("[yellow]tts service isn't running "
                          "(start it: docker compose --profile voice up -d tts)[/yellow]")
            return
        vs = self.voice.list_voices()
        if not vs:
            console.print("[yellow]no voices yet - drop a 10-30s clip at "
                          "./voices/default.wav[/yellow]")
            return
        console.print("voices: " + ", ".join(
            f"[green]{v}[/green]" if v == self.voice.voice else v for v in vs))

    def cmd_export(self, fmt: str = "md") -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out = Path("/transcripts/exports")
        out.mkdir(parents=True, exist_ok=True)
        name = f"hermes-{self.transcript.session_id}-{stamp}"
        if fmt == "md":
            p = out / f"{name}.md"
            p.write_text(self.transcript.as_markdown(), encoding="utf-8")
        elif fmt == "json":
            import json as _json

            p = out / f"{name}.json"
            p.write_text(_json.dumps(self.transcript.read(), indent=2), encoding="utf-8")
        else:
            p = out / f"{name}.txt"
            lines = [
                f"[{r.get('role')}] {r.get('content', '')}"
                for r in self.transcript.read()
                if r.get("content")
            ]
            p.write_text("\n\n".join(lines), encoding="utf-8")
        console.print(f"[green]exported[/green] -> transcripts/exports/{p.name}")

    def maybe_speak(self, text: str) -> None:
        if not self.speak or not text.strip():
            return
        if not self.voice.available():
            console.print("[dim](tts offline - not speaking)[/dim]")
            return
        AUDIO_OUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%H%M%S")
        path = AUDIO_OUT / f"hermes-{stamp}.wav"
        try:
            self.voice.speak(text[:2000], out_path=path, normalize=True)
            console.print(f"[dim]spoken -> workspace/audio/{path.name}[/dim]")
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]tts failed: {e}[/red]")

    # -- loop -----------------------------------------------------------------

    def handle_command(self, line: str) -> bool:
        """Returns False when the app should exit."""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            self.cmd_help()
        elif cmd == "/jobs":
            self.cmd_jobs()
        elif cmd == "/system":
            self.cmd_system()
        elif cmd == "/usage":
            self.cmd_usage()
        elif cmd == "/models":
            self.cmd_models(arg)
        elif cmd == "/health":
            self.cmd_health()
        elif cmd in ("/history", "/trends"):
            self.cmd_history(arg)
        elif cmd == "/stats":
            self.cmd_stats(arg)
        elif cmd == "/job":
            if not arg:
                console.print(f"current job: [green]{self.job}[/green]")
            else:
                self.job = arg
                self.agent.job = arg
                self.transcript.event("job_changed", job=arg)
                console.print(f"job -> [green]{arg}[/green]")
        elif cmd == "/auto":
            self.auto = not self.auto
            self.agent.auto_job = self.auto
            console.print(f"auto job selection: [green]{'on' if self.auto else 'off'}[/green]")
        elif cmd == "/voice":
            if arg:
                self.voice.voice = arg
                console.print(f"voice -> [green]{arg}[/green]")
            else:
                console.print(f"current voice: [green]{self.voice.voice}[/green]")
        elif cmd == "/voices":
            self.cmd_voices()
        elif cmd == "/speak":
            self.speak = not self.speak
            console.print(f"speaking: [green]{'on' if self.speak else 'off'}[/green]")
        elif cmd == "/confirm":
            self.confirm_mutations = not self.confirm_mutations
            console.print(
                f"confirm before writes/shell: "
                f"[green]{'on' if self.confirm_mutations else 'off'}[/green]"
            )
        elif cmd == "/new":
            self.transcript = Transcript(surface="hermes")
            self.agent.transcript = self.transcript
            self.agent.reset()
            console.print("[green]new session[/green]")
        elif cmd == "/transcript":
            console.print(f"transcript: [cyan]{self.transcript.path}[/cyan]")
            console.print("[dim](on your host: ./transcripts/...)[/dim]")
        elif cmd == "/export":
            self.cmd_export(arg or "md")
        elif cmd == "/sh":
            from . import tools

            console.print(tools.run_shell(arg or "ls -la"))
        elif cmd == "/clear":
            console.clear()
        else:
            console.print(f"[red]unknown command {cmd}[/red] - try /help")
        return True

    def _router_line(self) -> str:
        """What to say about the router on the banner.

        NOT `self.router.base_url`. That is `http://litellm:4000/v1`, which is a
        hostname on the Docker network -- correct for this process, and useless
        to the human reading it. Printed on screen it looks like an address you
        could use, so you paste it into a browser or VS Code on Windows and it
        fails, because `litellm` does not resolve outside Docker.

        So say the thing that is actually useful: whether the router is
        answering, how many jobs it offers, and the address that works from
        WINDOWS if you want to point an editor at it.
        """
        try:
            jobs = self.router.available_jobs()
        except Exception:  # noqa: BLE001  any router failure is reported as 'not answering'
            return ("router [red]not answering[/red] - is it up?  "
                    "verify.cmd  or  logs.cmd litellm")

        n = len(jobs)
        # Say "base URL", not just the address. Printed bare it reads like a
        # link, so it gets opened in a browser -- which returns
        # {"detail":"Not Found"}, because nothing is routed at exactly /v1.
        # It is a PREFIX: tools append /chat/completions themselves. The page
        # you can actually open is http://localhost:4000/ (the API docs).
        return (f"router [green]ok[/green] - {n} job{'s' if n != 1 else ''}"
                f"  ·  base URL for editors: http://localhost:4000/v1"
                f"  ·  docs page: http://localhost:4000/")

    def run(self) -> None:
        ui.banner(self.job, "/workspace  (= ai-stack\\workspace on Windows)",
                  self._router_line())

        # Say the routing mode out loud at startup. It used to be signalled
        # only by an asterisk in the prompt -- `hermes(chat*)` -- which is
        # easy to miss and impossible to guess the meaning of. Whether a
        # machine is choosing your model for you is worth one clear line.
        if self.auto:
            console.print(
                "  [green]auto[/green] - the route is picked per message; "
                "the prompt shows which one was used.\n"
                "         [dim]/auto turns it off for this session; "
                "AUTO_JOB=false in .env makes that permanent.[/dim]"
            )
        else:
            console.print(
                f"  [yellow]manual[/yellow] - every message goes to "
                f"[green]{self.job}[/green].  [dim]/auto to choose "
                f"automatically instead.[/dim]"
            )

        while True:
            try:
                marker = f"{self.job}{'*' if self.auto else ''}"
                line = self.session.prompt(f"hermes({marker}) > ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                return

            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    console.print("bye")
                    return
                continue

            try:
                with ui.status("thinking"):
                    reply = self.agent.send(line)
            except KeyboardInterrupt:
                console.print("[yellow]cancelled[/yellow]")
                continue
            except Exception as e:  # noqa: BLE001
                ui.error(f"{type(e).__name__}: {e}",
                         "is the router up?  verify.cmd")
                continue

            ui.say(reply, subtitle=f"{self.job}{'*' if self.auto else ''}")
            self.maybe_speak(reply)
            self.agent.trim()


def main() -> int:
    try:
        Hermes().run()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]fatal:[/red] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
