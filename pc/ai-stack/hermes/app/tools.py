"""
Hermes tool belt.

Everything is confined to /workspace (the folder you mounted from the host).
Paths that try to escape it are rejected -- the agent can't wander into the
container's system files, and it definitely can't see the rest of your machine.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace")).resolve()
MAX_READ = 200_000
MAX_OUTPUT = 20_000


class ToolError(Exception):
    pass


def _safe(path: str) -> Path:
    p = (ROOT / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise ToolError(f"path escapes the workspace: {path}")
    return p


def _clip(s: str, limit: int = MAX_OUTPUT) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated, {len(s) - limit} more chars]"


# -- implementations ----------------------------------------------------------


def list_dir(path: str = ".") -> str:
    p = _safe(path)
    if not p.exists():
        raise ToolError(f"no such directory: {path}")
    if p.is_file():
        return f"{p.relative_to(ROOT)} (file, {p.stat().st_size} bytes)"
    rows = []
    for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            rows.append(f"{child.name}/")
        else:
            rows.append(f"{child.name}  ({child.stat().st_size} B)")
    return "\n".join(rows) or "(empty)"


def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    p = _safe(path)
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    if p.stat().st_size > MAX_READ:
        raise ToolError(f"file too large ({p.stat().st_size} bytes); read a slice instead")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lo = max(1, start_line) - 1
    hi = end_line if end_line and end_line > lo else len(lines)
    body = "\n".join(f"{i + 1:>5}| {ln}" for i, ln in enumerate(lines[lo:hi], start=lo))
    return _clip(body)


def write_file(path: str, content: str) -> str:
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {p.relative_to(ROOT)}"


def edit_file(path: str, find: str, replace: str) -> str:
    p = _safe(path)
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    text = p.read_text(encoding="utf-8")
    count = text.count(find)
    if count == 0:
        raise ToolError("the `find` text was not present in the file")
    if count > 1:
        raise ToolError(f"`find` matched {count} times; make it unique")
    p.write_text(text.replace(find, replace, 1), encoding="utf-8")
    return f"edited {p.relative_to(ROOT)}"


def search(pattern: str, path: str = ".", glob: str = "*") -> str:
    p = _safe(path)
    cmd = ["grep", "-rIn", "--color=never", "--include", glob, "-e", pattern, str(p)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    out = r.stdout.strip()
    if not out:
        return "no matches"
    out = out.replace(str(ROOT) + "/", "")
    return _clip(out)


def run_shell(command: str, timeout: int = 120) -> str:
    """Runs inside the Hermes container, cwd=/workspace. Not your host shell."""
    r = subprocess.run(
        ["/bin/bash", "-lc", command],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    parts = []
    if r.stdout.strip():
        parts.append(r.stdout.strip())
    if r.stderr.strip():
        parts.append(f"[stderr]\n{r.stderr.strip()}")
    parts.append(f"[exit {r.returncode}]")
    return _clip("\n".join(parts))


# A plain httpx request identifies itself as "python-httpx/x.x.x", which a
# lot of ordinary sites -- food blogs especially -- refuse outright with a 403
# before they have looked at anything else about the request. This is the same
# fix Kitchen's importer needed for the same reason (see its core.py): a real
# browser's User-Agent plus the rest of the headers a real navigation sends,
# because a filter looks at the SET, not just the one header people remember
# to fake.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_BROWSER_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def http_get(url: str) -> str:
    import httpx

    if not url.startswith(("http://", "https://")):
        raise ToolError("url must start with http:// or https://")
    try:
        r = httpx.get(url, timeout=30.0, follow_redirects=True,
                      headers=_BROWSER_HEADERS)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        # SAID PLAINLY, because this is what reaches the model as the tool's
        # result, and the model can only be as clear with the person as this
        # is with the model. "error: HTTPStatusError: ..." on its own is what
        # produced a vague "try unlocking the site" non-answer; telling it
        # what a 403/429/404 actually MEANS gives it something to say instead.
        if code == 403:
            raise ToolError(
                f"{url} refused this request (403) even with browser-like "
                "headers. Some sites only serve pages to a signed-in "
                "browser, or block anything automated outright -- there is "
                "no header that gets past that from here.")
        if code == 429:
            raise ToolError(
                f"{url} answered 429 (rate-limited) -- it is saying 'not so "
                "fast', not 'no'. Worth trying again in a minute.")
        raise ToolError(f"{url} answered {code}.")
    except httpx.RequestError as e:
        raise ToolError(f"could not reach {url} ({e.__class__.__name__}).")
    return _clip(r.text, 15_000)


# -- schema -------------------------------------------------------------------

REGISTRY: dict[str, Callable[..., str]] = {
    "list_dir": list_dir,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "search": search,
    "run_shell": run_shell,
    "http_get": http_get,
}

# The house tools -- see house.py's header. Registered here so the agent
# loop needs no changes to grow eyes.
from . import house as _house

REGISTRY.update(_house.REGISTRY)

from . import plant_tools as _plant

REGISTRY.update(_plant.REGISTRY)

from . import api_tools as _apis

REGISTRY.update(_apis.REGISTRY)

from . import doc_tools as _docs

REGISTRY.update(_docs.REGISTRY)


def _tool(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


SCHEMA: list[dict[str, Any]] = [
    _tool(
        "list_dir",
        "List files and folders in the workspace.",
        {"path": {"type": "string", "description": "Relative path. Default '.'"}},
        [],
    ),
    _tool(
        "read_file",
        "Read a text file from the workspace, with line numbers.",
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-indexed, optional"},
            "end_line": {"type": "integer", "description": "optional"},
        },
        ["path"],
    ),
    _tool(
        "write_file",
        "Create or overwrite a file in the workspace.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
    _tool(
        "edit_file",
        "Replace one unique snippet in a file. Prefer this over rewriting whole files.",
        {
            "path": {"type": "string"},
            "find": {"type": "string", "description": "Exact text, must be unique"},
            "replace": {"type": "string"},
        },
        ["path", "find", "replace"],
    ),
    _tool(
        "search",
        "Recursive text search across the workspace (grep).",
        {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string", "description": "e.g. '*.py'"},
        },
        ["pattern"],
    ),
    _tool(
        "run_shell",
        "Run a bash command inside the agent container, cwd=/workspace.",
        {"command": {"type": "string"}, "timeout": {"type": "integer"}},
        ["command"],
    ),
    _tool(
        "http_get",
        "Fetch a URL and return its body as text.",
        {"url": {"type": "string"}},
        ["url"],
    ),
]

SCHEMA += [
    _tool("house_status",
          "Is everything up? Current state of every household service monitor.",
          {}, []),
    _tool("photos_stats",
          "The family photo library: who has how many photos and videos.",
          {}, []),
    _tool("tribute_status",
          "The Pee-Wee Harris tribute: who is on the dial and what each "
          "person still needs (tellings, photos, narration).",
          {}, []),
    _tool("map_places",
          "The travel scratch-map: where somebody has been. Empty person "
          "lists everyone's counts.",
          {"person": {"type": "string",
                      "description": "first name: jason, casey, mom or robin"}},
          []),
    _tool("pending_read",
          "The household's standing to-do list (PENDING.md), verbatim.",
          {}, []),
    _tool("pending_add",
          "Add one item to the household to-do list. Append-only.",
          {"item": {"type": "string"}}, ["item"]),
    _tool("logs_search",
          "Search the last N minutes of every container's logs on both "
          "machines. Filter by text and/or container name.",
          {"text": {"type": "string"}, "container": {"type": "string"},
           "minutes": {"type": "integer"}}, []),
    _tool("find_search",
          "Search everything of Jason's in one place -- recipes, workouts, "
          "documents and his own source code. Returns matches with an id "
          "each. Use this FIRST to find what is relevant, then find_read on "
          "the ids worth reading in full. Optional kind: recipe, workout, "
          "document or code.",
          {"query": {"type": "string"},
           "kind": {"type": "string",
                    "description": "recipe | workout | document | code"},
           "limit": {"type": "integer", "description": "1-25, default 10"}},
          ["query"]),
    _tool("find_read",
          "The full text of ONE indexed item, by an id from find_search. "
          "This is how you build context: search to see what exists, then "
          "read the few that matter rather than guessing from snippets.",
          {"item_id": {"type": "string", "description": "An id from find_search"}},
          ["item_id"]),
    _tool("find_passages",
          "Passages from Jason's and Sachin's TEXTBOOKS, cut to a paragraph "
          "each and carrying the page they are on. Use this -- not "
          "find_search -- for any clinical or study question: it returns the "
          "book's own words with a citable page, where find_search returns "
          "whole files. Quote what comes back and cite the page. If it says "
          "nothing matches, say nothing matches; do not answer from memory. "
          "TWO THINGS DECIDE WHETHER THIS WORKS. (1) Search with the "
          "distinctive technical terms, not a whole sentence -- 'selective "
          "differential media MacConkey lactose' beats 'what is the "
          "difference between selective and differential media', because the "
          "index is keyword-based and the shelf is mostly IT books, so common "
          "words rank networking manuals above the textbook. (2) Narrow with "
          "`book` or `shelf` whenever you know which one; call `shelves` to "
          "see the names.",
          {"question": {"type": "string"},
           "limit": {"type": "integer", "description": "1-20, default 6"},
           "book": {"type": "string",
                    "description": "Optional: a book's name, as a person "
                                   "would say it (e.g. 'Lammert')"},
           "shelf": {"type": "string",
                     "description": "Optional: a shelf name from `shelves` "
                                    "(e.g. 'sachin'). Ignored if `book` is "
                                    "given."}},
          ["question"]),
    _tool("shelves",
          "The named shelves the textbooks are filed under, with how many "
          "passages each holds. Call this when you need to narrow a study "
          "question and do not know what the shelves are called.",
          {}, []),
    _tool("logs_errors",
          "Errors, panics and failures across every container in the last "
          "N minutes (default 30). The 'what just broke?' tool.",
          {"minutes": {"type": "integer"}}, []),
    _tool("zee_notes",
          "Search Zanetta's career recordings and conversations. Her "
          "uploaded audio is transcribed and her spoken questions are "
          "kept, so this finds what she actually said about a topic. "
          "Read-only. Empty query returns her most recent notes.",
          {"query": {"type": "string",
                     "description": "topic or phrase to look for"},
           "limit": {"type": "integer"}}, []),
    _tool("zee_growth",
          "The moments Zanetta has marked as GROWTH -- what she has "
          "learned or achieved, in her own words, newest first. Use this "
          "for 'how is Casey getting on' rather than searching everything.",
          {"limit": {"type": "integer"}}, []),
    _tool("zee_progress",
          "Where Zanetta is up to with her career record: how many "
          "portfolio entries and at what status, what is FINISHED BUT NOT "
          "SUBMITTED, what has gone untouched, and which awards she is "
          "aiming at. The answer to 'how is Casey getting on'. Read-only.",
          {}, []),
    _tool("git_commits",
          "Latest commits in both household repos -- what changed lately.",
          {"n": {"type": "integer"}}, []),
    _tool("git_file",
          "Read a file (or list a directory) from a household repo. repo is "
          "'site' or 'nuc'; path is repo-relative.",
          {"repo": {"type": "string"}, "path": {"type": "string"}},
          ["repo", "path"]),
    _tool("plant_lookup",
          "Look up a plant by name or USDA symbol in the USDA PLANTS database. "
          "Returns scientific name, common names, family, and native status. "
          "Use for 'what is X plant', 'is Y native', or any plant-facts question.",
          {"query": {"type": "string",
                     "description": "Common name, scientific name, or USDA symbol (e.g. ACRU)"},
           "limit": {"type": "integer",
                     "description": "Max results, 1-20, default 5"}},
          ["query"]),
    _tool("plant_identify",
          "Identify a plant from a publicly accessible photo URL using PlantNet. "
          "Returns the top 3 candidates with confidence scores. "
          "Use when someone shares or describes a photo and wants to know what plant it is.",
          {"image_url": {"type": "string",
                         "description": "Publicly accessible URL of the plant photo"},
           "organs": {"type": "string",
                      "description": "Plant part in the photo: auto, leaf, flower, fruit, bark"}},
          ["image_url"]),
    _tool("perenual_search",
          "Plant care data (watering, sunlight, edible, poisonous) from Perenual. "
          "Use for 'how do I care for X'. Free tier; a paid-only field may be missing.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("trefle_search",
          "Plant names, family and taxonomy from Trefle.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("plantbook_search",
          "Ideal light, temperature, humidity and soil ranges for a species from Open Plantbook.",
          {"query": {"type": "string", "description": "3+ characters"}, "limit": {"type": "integer"}},
          ["query"]),
    _tool("permapeople_search",
          "Permaculture plant facts and companions from Permapeople (credit Permapeople.org).",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("inat_taxa",
          "Taxa, common names and observation counts from iNaturalist. No key.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("web_search",
          "Search the web. engine 'serper' (Google) or 'tavily'. Each call spends free "
          "credits, so try docs_search and the plant tools first.",
          {"query": {"type": "string"},
           "engine": {"type": "string", "description": "serper (default) or tavily"},
           "limit": {"type": "integer"}}, ["query"]),
    _tool("docs_search",
          "Search the saved reference documents (how each free API is called, "
          "plus our own setup docs). USE THIS FIRST for any 'how do I call "
          "API X' question instead of answering from memory. Search with "
          "distinctive words (API name, endpoint, error code). Returns "
          "snippets with the document title and an id.",
          {"query": {"type": "string"},
           "source": {"type": "string",
                      "description": "Optional tag: api-docs or homelab-docs"},
           "limit": {"type": "integer", "description": "1-10, default 5"}},
          ["query"]),
    _tool("docs_read",
          "The text of ONE saved document by id from docs_search.",
          {"doc_id": {"type": "integer"},
           "max_chars": {"type": "integer", "description": "500-20000, default 6000"}},
          ["doc_id"]),
]

# Tools that change things. The CLI asks before running these when
# confirmation mode is on.
MUTATING = {"write_file", "edit_file", "run_shell", "pending_add"}


def dispatch(name: str, arguments: str | dict) -> str:
    fn = REGISTRY.get(name)
    if fn is None:
        return f"error: unknown tool '{name}'"
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return f"error: could not parse arguments as JSON: {arguments[:200]}"
    else:
        args = arguments or {}
    try:
        return str(fn(**args))
    except ToolError as e:
        return f"error: {e}"
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"
    except subprocess.TimeoutExpired:
        return "error: command timed out"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {e}"
