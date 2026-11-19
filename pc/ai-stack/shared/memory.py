"""
Per-person memory for Hermes: markdown files in a directory that person owns.

WHY FILES AND NOT A MEMORY FRAMEWORK
Letta, mem0 and LangChain's memory all want a database. This household has
already been bitten by that: SQLite over a network share corrupts, because
POSIX advisory locks and the -shm mmap do not survive SMB/NFS. The memory
root is meant to live on the NAS, in each person's own home directory, so a
database there would be rebuilding a known failure on purpose.

Markdown has none of that. It is greppable, hand-editable, survives the
container being rebuilt, and can be fixed with a text editor at 2am. It is
also the same philosophy people.json already states about itself -- "who can
get in is answerable by looking, and recoverable by editing, without either
service running". What Hermes remembers about somebody should be answerable
the same way: open the folder and read it.

WHAT IS STORED, AND WHERE

    <root>/<person>/
        MEMORY.md          the index. One line per fact. Loaded every turn.
        facts/<slug>.md    one fact per file, with frontmatter.
        log/YYYY-MM.md     append-only record of what happened.
        .embeddings.json   a CACHE for recall. Delete it any time.

MEMORY.md is the part the model sees on every turn, so it must stay small --
it is an index of what is known, not the knowledge itself. facts/ holds the
detail and is pulled in only when a query matches.

THE SECURITY PROPERTY, STATED PLAINLY
The directory is derived from the AUTHENTICATED identity and never from
anything the person typed. `_slug()` refuses anything that is not a plain
local name, so no message can steer a read or a write into somebody else's
folder. That is the whole access-control story on the Hermes side; when the
root is a NAS home the filesystem enforces it a second time.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# One fact per file, and the filename is derived from the fact, so two
# recordings of the same thing overwrite rather than accumulate.
_SLUG_OK = re.compile(r"[^a-z0-9]+")

# A person key must be a single, ordinary path component. Anything with a
# separator, a traversal, a NUL, or a leading dot is refused outright rather
# than sanitised -- quietly "fixing" a hostile path is how you end up writing
# somewhere you did not mean to.
_PERSON_OK = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,96}$")


def _slug(text: str, limit: int = 60) -> str:
    s = _SLUG_OK.sub("-", (text or "").strip().lower()).strip("-")
    return (s[:limit].rstrip("-")) or "untitled"


# What an identity must look like BEFORE it is turned into a directory name.
# Validating the input rather than only the output is the point: slugifying
# "../../etc/passwd" produces "etc-passwd", which is a perfectly well-formed
# directory name and therefore passes every check applied afterwards. It does
# not traverse -- but it means a string that should never have reached this
# function is silently accepted and given a folder. Refuse it at the door.
_EMAIL_OK = re.compile(
    r"^[^@\s/\\]{1,64}@[^@\s/\\]{1,190}\.[a-z]{2,}$", re.IGNORECASE)

# Characters that must never appear in an identity, checked separately so the
# reason for each is visible: a path separator, a traversal, a NUL (which
# truncates a filename in any C-based syscall), and whitespace.
_FORBIDDEN = ("..", "/", "\\", "\x00", " ", "\t", "\n")


def person_key(email: str) -> str:
    """The directory name for an SSO identity. Raises on anything else.

    Derived from the email because that is what people.json keys on and what
    the OIDC id_token carries. '@' and '.' become '-' so the name is safe on
    every filesystem this might land on, including exFAT and SMB shares that
    dislike '@'.

    THIS REFUSES RATHER THAN SANITISES. The caller should only ever be
    passing an authenticated identity; if it is passing something else, that
    is a bug upstream and a loud failure is how it gets found. Quietly
    turning a hostile string into a tidy folder name hides it instead.
    """
    raw = (email or "").strip().lower()
    if any(bad in raw for bad in _FORBIDDEN) or not _EMAIL_OK.match(raw):
        raise ValueError(f"not an email-shaped identity: {email!r}")
    key = _SLUG_OK.sub("-", raw).strip("-")
    if not _PERSON_OK.match(key):
        raise ValueError(f"refusing unusable person key from {email!r}")
    return key


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then replace.

    A half-written fact is worse than a missing one: the index would claim
    knowledge the file no longer contains. os.replace is atomic on the same
    filesystem, which is why the temp file is a sibling and not in /tmp.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


@dataclass
class Fact:
    slug: str
    summary: str
    body: str
    kind: str = "note"
    created: str = field(default_factory=_now)
    source: str = "explicit"          # explicit | distilled

    def to_markdown(self) -> str:
        return (
            "---\n"
            f"slug: {self.slug}\n"
            f"summary: {self.summary}\n"
            f"kind: {self.kind}\n"
            f"created: {self.created}\n"
            f"source: {self.source}\n"
            "---\n\n"
            f"{self.body.strip()}\n"
        )

    @classmethod
    def from_markdown(cls, path: Path) -> Fact | None:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        meta: dict[str, str] = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) == 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = parts[2]
        return cls(
            slug=meta.get("slug", path.stem),
            summary=meta.get("summary", path.stem),
            body=body.strip(),
            kind=meta.get("kind", "note"),
            created=meta.get("created", ""),
            source=meta.get("source", "explicit"),
        )


class MemoryStore:
    """One person's memory. Construct it from an authenticated identity only."""

    def __init__(self, root: str | Path, email: str, name: str = "") -> None:
        self.key = person_key(email)
        self.email = email
        self.name = name or email
        self.dir = Path(root).expanduser() / self.key
        self.facts_dir = self.dir / "facts"
        self.log_dir = self.dir / "log"
        self.index = self.dir / "MEMORY.md"
        self.cache = self.dir / ".embeddings.json"

    # -- setup ---------------------------------------------------------------

    def ensure(self) -> None:
        """Create the folder the first time this person says anything.

        Deliberately lazy: a person who has never used Hermes should not have
        an empty memory folder sitting in their home directory implying
        otherwise.
        """
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not self.index.exists():
            _atomic_write(self.index, (
                f"# What Hermes knows about {self.name}\n\n"
                f"<!-- {self.email} -->\n\n"
                "One line per fact. The detail is in `facts/`. This file is\n"
                "loaded on every turn, so keep it an index, not an archive.\n"
                "Delete a line and its file to make Hermes forget something.\n\n"
            ))

    # -- writing -------------------------------------------------------------

    def remember(self, summary: str, body: str = "", *,
                 kind: str = "note", source: str = "explicit") -> Fact:
        """Record one fact. Re-recording the same summary replaces it."""
        self.ensure()
        fact = Fact(slug=_slug(summary), summary=summary.strip(),
                    body=(body or summary).strip(), kind=kind, source=source)
        _atomic_write(self.facts_dir / f"{fact.slug}.md", fact.to_markdown())
        self._reindex()
        self._drop_cache()
        return fact

    def forget(self, slug: str) -> bool:
        f = self.facts_dir / f"{_slug(slug)}.md"
        if not f.exists():
            return False
        f.unlink()
        self._reindex()
        self._drop_cache()
        return True

    def facts(self) -> list[Fact]:
        if not self.facts_dir.exists():
            return []
        out = [Fact.from_markdown(p) for p in sorted(self.facts_dir.glob("*.md"))]
        return [f for f in out if f]

    def _reindex(self) -> None:
        """Rebuild MEMORY.md from the files, so the index cannot drift.

        The index is never the source of truth -- the files are. Editing a
        fact by hand and having the index disagree is exactly the kind of
        silent wrongness this design exists to avoid.
        """
        lines = [
            f"# What Hermes knows about {self.name}\n",
            f"<!-- {self.email} -->\n",
            ("One line per fact. Detail lives in `facts/`. Delete a line and\n"
            "its file to make Hermes forget something.\n"),
        ]
        for f in self.facts():
            mark = "" if f.source == "explicit" else " _(inferred)_"
            lines.append(f"- **{f.summary}** — `facts/{f.slug}.md`{mark}")
        _atomic_write(self.index, "\n".join(lines) + "\n")

    # -- the activity log ----------------------------------------------------

    def log_turn(self, surface: str, user_text: str, reply: str,
                 tools: Iterable[str] = ()) -> None:
        """Append one exchange to this month's log.

        Append mode with a single write call: two surfaces (the CLI and the
        Telegram bridge) can be talking to the same person at once, and an
        O_APPEND write under the pipe buffer size will not interleave. This
        is also why the log is one file per MONTH rather than one per day --
        fewer files to fsync on a network share, still small enough to read.
        """
        self.ensure()
        path = self.log_dir / f"{time.strftime('%Y-%m')}.md"
        used = ", ".join(tools)
        entry = (
            f"\n## {_now()} · {surface}\n\n"
            f"**{self.name}:** {user_text.strip()[:2000]}\n\n"
            f"**Hermes:** {reply.strip()[:2000]}\n"
        )
        if used:
            entry += f"\n_tools: {used}_\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry)

    def recent_log(self, chars: int = 6000) -> str:
        """The tail of the most recent log file, for distillation."""
        files = sorted(self.log_dir.glob("*.md")) if self.log_dir.exists() else []
        if not files:
            return ""
        return files[-1].read_text(encoding="utf-8", errors="replace")[-chars:]

    # -- recall --------------------------------------------------------------

    def _drop_cache(self) -> None:
        self.cache.unlink(missing_ok=True)

    def _embeddings(self, embed) -> dict[str, list[float]]:
        """Embeddings for every fact, cached beside them.

        Keyed by slug AND summary, so editing a fact's summary by hand
        invalidates just that entry rather than silently recalling the old
        meaning.
        """
        facts = self.facts()
        want = {f.slug: f"{f.summary}\n{f.body}"[:2000] for f in facts}
        cached: dict[str, Any] = {}
        if self.cache.exists():
            try:
                cached = json.loads(self.cache.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
        out: dict[str, list[float]] = {}
        missing = []
        for slug, text in want.items():
            hit = cached.get(slug)
            if hit and hit.get("text") == text:
                out[slug] = hit["vec"]
            else:
                missing.append((slug, text))
        if missing:
            vecs = embed([t for _, t in missing])
            for (slug, text), vec in zip(missing, vecs):
                out[slug] = vec
                cached[slug] = {"text": text, "vec": vec}
            try:
                _atomic_write(self.cache, json.dumps(cached))
            except OSError:
                pass  # a cache that cannot be written is not an error
        return out

    def recall(self, query: str, embed, k: int = 5) -> list[Fact]:
        """The k facts most similar to the query.

        `embed` is a callable taking a list of strings and returning a list of
        vectors -- passed in rather than imported so this module never needs
        to know about LiteLLM, and so tests can hand it a stub.
        """
        facts = {f.slug: f for f in self.facts()}
        if not facts:
            return []
        try:
            vecs = self._embeddings(embed)
            qv = embed([query])[0]
        except Exception:  # noqa: BLE001  falls back to substring search, see below
            # Retrieval is a nicety; never let it take the conversation down.
            # Fall back to substring matching, which is better than nothing
            # and costs no model call.
            q = query.lower()
            hit = [f for f in facts.values()
                   if q in f.summary.lower() or q in f.body.lower()]
            return hit[:k]

        def cos(a: list[float], b: list[float]) -> float:
            num = sum(x * y for x, y in zip(a, b))
            da = sum(x * x for x in a) ** 0.5
            db = sum(y * y for y in b) ** 0.5
            return num / (da * db) if da and db else 0.0

        ranked = sorted(
            ((cos(qv, v), s) for s, v in vecs.items()), reverse=True)
        return [facts[s] for _, s in ranked[:k] if s in facts]

    # -- what the model sees -------------------------------------------------

    def context_block(self, query: str = "", embed=None, k: int = 5) -> str:
        """The memory to prepend to a turn: the index, plus matching detail.

        The index is always included because it is small and tells the model
        what it could ask about. The bodies are only included for facts the
        query actually matches, because that is the part that grows without
        bound.
        """
        if not self.index.exists():
            return ""
        parts = [self.index.read_text(encoding="utf-8", errors="replace").strip()]
        if query and embed is not None:
            hits = self.recall(query, embed, k=k)
            if hits:
                parts.append("\n## Relevant detail\n")
                for f in hits:
                    parts.append(f"### {f.summary}\n{f.body}\n")
        return "\n".join(parts).strip()
