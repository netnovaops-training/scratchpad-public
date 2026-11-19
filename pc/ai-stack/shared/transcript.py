"""
Shared transcript log.

Every surface (Hermes CLI, Telegram bridge, exporter) writes here. One JSONL
file per session, append-only, plain text, no database. Grep it, tail it,
back it up, hand it to another tool -- it's just lines of JSON.

Layout:
    /transcripts/<surface>/<YYYY-MM-DD>/<session_id>.jsonl

Each line:
    {"ts": "...", "session": "...", "surface": "hermes", "role": "user",
     "content": "...", "model": "coding", "job": "coding", "meta": {...}}
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# A surface name or session id becomes a folder / file name. 2026-11-19 (CodeQL py/path-injection): the
# hermes server passes the request's surface straight in, so "../x" could have written outside the
# transcript root. Only plain names are accepted now; the real callers use "hermes", "telegram", app names
# and uuid hex, which all match.
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")


def _plain(value: str, what: str) -> str:
    """Return value if it is a plain file name (no slashes, no leading dot), else raise ValueError."""
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"transcript {what} must be a plain name: {value!r}")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Transcript:
    def __init__(
        self,
        surface: str,
        session_id: str | None = None,
        root: str | None = None,
        title: str | None = None,
    ) -> None:
        self.surface = _plain(surface, "surface")
        self.session_id = _plain(session_id or uuid.uuid4().hex[:12], "session id")
        self.root = Path(root or os.environ.get("TRANSCRIPT_DIR", "/transcripts")).resolve()
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        self.dir = (self.root / self.surface / day).resolve()
        if not self.dir.is_relative_to(self.root):  # belt and braces after _plain()
            raise ValueError("transcript folder is outside TRANSCRIPT_DIR")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.session_id}.jsonl"
        self.title = title
        if not self.path.exists():
            self._write(
                {
                    "ts": _now(),
                    "session": self.session_id,
                    "surface": surface,
                    "role": "system",
                    "event": "session_start",
                    "content": title or "",
                }
            )

    # -- internals ------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # -- public ---------------------------------------------------------------

    def log(
        self,
        role: str,
        content: str,
        *,
        job: str | None = None,
        model: str | None = None,
        **meta: Any,
    ) -> None:
        """Record one turn. role is user | assistant | system | tool."""
        rec: dict[str, Any] = {
            "ts": _now(),
            "session": self.session_id,
            "surface": self.surface,
            "role": role,
            "content": content,
        }
        if job:
            rec["job"] = job
        if model:
            rec["model"] = model
        if meta:
            rec["meta"] = meta
        self._write(rec)

    def event(self, name: str, **meta: Any) -> None:
        self._write(
            {
                "ts": _now(),
                "session": self.session_id,
                "surface": self.surface,
                "role": "system",
                "event": name,
                "meta": meta or {},
            }
        )

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def as_markdown(self) -> str:
        """Human-readable rendering of this session."""
        rows = self.read()
        lines = [f"# Transcript - {self.surface} / {self.session_id}", ""]
        for r in rows:
            if r.get("event"):
                lines.append(f"_{r['ts']} - {r['event']}_")
                lines.append("")
                continue
            role = r.get("role", "?")
            tag = {"user": "You", "assistant": "Assistant", "tool": "Tool"}.get(
                role, role.title()
            )
            model = f" `{r.get('job') or r.get('model')}`" if (r.get("job") or r.get("model")) else ""
            lines.append(f"**{tag}**{model}  <sub>{r['ts']}</sub>")
            lines.append("")
            lines.append(r.get("content", ""))
            lines.append("")
        return "\n".join(lines)
