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
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


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
        self.surface = surface
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.root = Path(root or os.environ.get("TRANSCRIPT_DIR", "/transcripts"))
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        self.dir = self.root / surface / day
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
