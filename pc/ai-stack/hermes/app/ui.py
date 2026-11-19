"""
How Hermes looks in the terminal.

Kept apart from cli.py on purpose: the REPL is about input and commands, this
is about presentation, and mixing them is how a terminal UI turns into
spaghetti. Everything the user sees goes through here.

The shape:

    ╭─ ⚡Hermes ───────────────────────────────────────────╮
    │ The answer, wrapped, with room to breathe.           │
    ╰──────────────────────────────────────────────────────╯
       📄 preparing file…
       📄 read      notes.md                    0.0s
       🖥  $         free -g                     0.1s [total 31Gi…]

Two ideas do most of the work:

  * The ANSWER gets a box. Tool activity does not. In a long session the thing
    you scroll back for is what Hermes said, and a box is what your eye finds.
    Boxing everything would make nothing stand out.

  * Every tool line is ONE line, always the same shape: icon, verb, target,
    time, a peek at the result. Scannable in a column. The full output already
    went to the model and to the transcript; repeating it on screen just buries
    the next thing.

Set HERMES_ASCII=1 for plain [+] markers instead of emoji -- older Windows
consoles render emoji as boxes, which looks worse than no emoji at all.
"""

from __future__ import annotations

import os

from rich.box import ROUNDED
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = Console()

ASCII = os.environ.get("HERMES_ASCII", "").lower() in ("1", "true", "yes")

BRAND = "Hermes" if ASCII else "⚡Hermes"
BORDER = "yellow"

# icon, short verb, and which argument names the thing being acted on.
_TOOLS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    #  name          emoji  ascii   verb     arg keys to show, in order
    "list_dir":     ("📁", "[d]", "ls",    ("path",)),
    "read_file":    ("📄", "[r]", "read",  ("path",)),
    "write_file":   ("📝", "[w]", "write", ("path",)),
    "edit_file":    ("📝", "[e]", "edit",  ("path",)),
    "search":       ("🔍", "[s]", "grep",  ("pattern", "path")),
    "run_shell":    ("💻", "[$]", "$",     ("command",)),
    "http_get":     ("🌐", "[h]", "http",  ("url",)),
}
_UNKNOWN = ("⚙", "[*]", "tool", ())

# "preparing X…" -- what the noun should be while a tool is running.
_PREPARING = {
    "list_dir": "files", "read_file": "file", "write_file": "file",
    "edit_file": "file", "search": "search", "run_shell": "terminal",
    "http_get": "request",
}


def _spec(name: str):
    return _TOOLS.get(name, _UNKNOWN)


def _icon(name: str) -> str:
    icon, ascii_icon, _, _ = _spec(name)
    return ascii_icon if ASCII else icon


def _clip(s: str, width: int) -> str:
    s = " ".join(str(s).split())          # collapse newlines and runs of spaces
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


# --- the answer --------------------------------------------------------------

def say(text: str, subtitle: str = "") -> None:
    """Hermes speaking. This is the thing that gets a box."""
    body = Markdown(text) if text.strip() else Text("(no reply)", style="dim italic")
    console.print(
        Panel(
            body,
            title=f"[bold {BORDER}]{BRAND}[/bold {BORDER}]",
            title_align="left",
            subtitle=(f"[dim]{subtitle}[/dim]" if subtitle else None),
            subtitle_align="right",
            border_style=BORDER,
            box=ROUNDED,
            padding=(0, 1),
        )
    )


def note(text: str) -> None:
    """A quiet aside from the program itself, not from the model."""
    console.print(f"[dim]  {text}[/dim]")


def error(text: str, hint: str = "") -> None:
    console.print(f"  [red]{text}[/red]")
    if hint:
        console.print(f"  [dim]{hint}[/dim]")


# --- tool activity -----------------------------------------------------------

def tool_start(name: str) -> None:
    """Printed the moment a tool is dispatched, so a slow one doesn't look dead."""
    noun = _PREPARING.get(name, name.replace("_", " "))
    console.print(f"  {_icon(name)} [dim]preparing {noun}…[/dim]")


def tool_done(name: str, args: dict, result: str, seconds: float) -> None:
    """One line: what ran, on what, how long, and a peek at what came back."""
    _, _, verb, keys = _spec(name)

    target = ""
    for k in keys:
        if args.get(k):
            target = str(args[k])
            break
    if not target:                        # a tool we don't have a spec for
        target = ", ".join(f"{k}={_clip(v, 20)}" for k, v in args.items())

    refused = result.startswith(("refused:", "error:"))

    # Fit the line to the terminal instead of letting it wrap. A wrapped tool
    # line breaks the column that makes these scannable in the first place --
    # and the full result is in the transcript anyway, so truncating loses
    # nothing that matters.
    width = max(60, console.width)
    target_w = max(16, min(38, width - 46))
    peek_w = max(0, width - (2 + 3 + 8 + target_w + 7 + 4))

    line = Text("  ")
    line.append(_icon(name) + " ")
    line.append(f"{verb:<7}", style="bold red" if refused else "bold cyan")
    line.append(f" {_clip(target, target_w):<{target_w}}", style="white")
    line.append(f" {seconds:5.1f}s", style="dim")
    if peek_w > 8:
        peek = _clip(result, peek_w)
        if peek:
            line.append(f"  [{peek}]", style="red" if refused else "dim")
    console.print(line, no_wrap=True, crop=True, overflow="ellipsis")


# --- chrome ------------------------------------------------------------------

def banner(job: str, workspace: str, extra: str = "") -> None:
    art = Text()
    art.append("  ⚡ " if not ASCII else "  ", style=BORDER)
    art.append("Hermes", style=f"bold {BORDER}")
    art.append("  ·  ", style="dim")
    art.append(f"job {job}", style="cyan")
    art.append("  ·  ", style="dim")
    art.append(workspace, style="dim")
    console.print()
    console.print(art)
    if extra:
        console.print(f"  [dim]{extra}[/dim]")
    console.print("  [dim]/help for commands, /exit to quit[/dim]")
    console.print()


def status(text: str = "thinking"):
    """Context manager for the spinner while the model is working."""
    return console.status(f"[dim]{text}…[/dim]", spinner="dots")
