"""/files — everyone sees what is theirs, and can take it away.

Jason, 2026-09-06: "if we need to make a unified location to see each user
that allows them to see all the files they individually own let's do that
like a /files proxy that they can manage their data and back it up on my
external SSD."

WHO OWNS WHAT IS DECIDED HERE, NOT GUESSED
The apps in this stack each keep their own files in their own volume, and
none of them records ownership in a way another app could read. So OWNERS
below is the mapping, written down once, and it is the only thing that
decides what anyone sees. A tree not listed for you does not appear and
cannot be fetched, whatever path is asked for.

Identity is the household SSO. Caddy's forward_auth has already verified
whoever this is before the request arrives, and copies X-Auth-Email in.
This app never sees a password and has no login of its own -- same shape
as every other app here.

THE VOLUMES ARE MOUNTED READ-ONLY, AND THAT IS DELIBERATE
"Manage their data" could mean delete, and delete is the one verb not
implemented. This is a web app reachable from the internet whose whole
job is to have every user's files in reach; giving it write access to all
of them means one bug or one stolen session can empty the lot. Download
and backup are safe under a read-only mount. If deleting from here turns
out to matter, it should be its own narrow route with its own thinking,
not a side effect of the mount flags.

BACKUP IS NOT DONE FROM IN HERE EITHER, AND FOR THE SAME REASON
The external SSD is on the NUC, which means pushing to it needs an SSH
key. An internet-facing container holding a key to the machine that
stores everyone's encrypted photos is a bad trade for saving a script.
So files-backup.sh runs on the host, and this app only READS the manifest
that script leaves behind -- enough to answer "is my stuff backed up?"
without holding anything that could do harm.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
ROOT_PATH = os.getenv("FILES_ROOT_PATH", "/files")
SITE_NAME = os.getenv("FILES_SITE_NAME", "Your files")
BACKUP_MANIFEST = Path(os.getenv("FILES_BACKUP_MANIFEST",
                                 "/backup-status/manifest.json"))

app = FastAPI(title="files", root_path=ROOT_PATH)
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# --- who owns what ----------------------------------------------------------
#
#  email -> [(label, path, what it is)]
#
#  Jason sees everything because he owns the hardware and pays for it.
#  Everyone else sees their own tree and nothing else. Kitchen is shared
#  household data rather than one person's, so it is listed for the people
#  who actually use it rather than assigned an owner.

HOUSEHOLD = [
    ("Recipes and food photos", "/data/kitchen",
     "Pictures and audio from the recipe app. Shared by the household."),
]

OWNERS: dict[str, list] = {
    "user@example.com": [
        ("Career portal", "/data/casey",
         ("Everything from your career portal: recordings you uploaded, "
         "answers that were spoken back, exports, and the twice-daily "
         "backups of your record.")),
    ] + HOUSEHOLD,
    "user@example.com": [
        ("Tea with Miss Dee", "/data/mom",
         "Your site's own files."),
    ] + HOUSEHOLD,
    "user@example.com": HOUSEHOLD,
    "user@example.com": [
        ("Casey's career portal", "/data/casey", "Her portal's files."),
        ("Mom's site", "/data/mom", "Her site's files."),
    ] + HOUSEHOLD,
}

# Files nobody needs to see and that only make the list harder to read.
HIDE = {".db-shm", ".db-wal", ".pyc"}
HIDE_NAMES = {"__pycache__", ".git", "lost+found"}


def who(request: Request) -> str:
    """Whoever Caddy vouched for. No header means Caddy is misconfigured."""
    email = (request.headers.get("X-Auth-Email") or "").strip().lower()
    if not email:
        # NOT a fallback to "some default user". If this app is ever
        # reachable without forward_auth in front of it, the correct
        # behaviour is to refuse rather than to serve somebody's files to
        # whoever asked.
        raise HTTPException(
            401, "This page has to be reached through the household sign-in.")
    return email


def trees_for(email: str) -> list:
    out = []
    for label, path, blurb in OWNERS.get(email, []):
        p = Path(path)
        if p.is_dir():
            out.append({"label": label, "root": path, "blurb": blurb,
                        "key": path.strip("/").replace("/", "-")})
    return out


def tree_by_key(email: str, key: str) -> dict:
    for t in trees_for(email):
        if t["key"] == key:
            return t
    raise HTTPException(404, "That is not one of your folders.")


def _safe(root: Path, rel: str) -> Path:
    """Resolve rel under root, or refuse.

    The check is on the RESOLVED path, not the string. Checking the string
    for ".." is the version that gets bypassed -- by symlinks, by
    encodings, by a name that legitimately contains dots. resolve() then
    is_relative_to() answers the only question that matters: does this end
    up inside the tree they own?
    """
    target = (root / rel.lstrip("/")).resolve()
    root = root.resolve()
    if not (target == root or target.is_relative_to(root)):
        raise HTTPException(403, "No.")
    return target


def _listing(base: Path, rel: str) -> dict:
    here = _safe(base, rel)
    if not here.is_dir():
        raise HTTPException(404, "No such folder.")
    dirs, files = [], []
    try:
        for p in sorted(here.iterdir(), key=lambda q: q.name.lower()):
            if p.name in HIDE_NAMES or p.suffix in HIDE:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            item = {"name": p.name,
                    "rel": str(Path(rel) / p.name).replace("\\", "/").lstrip("/"),
                    "size": st.st_size, "mtime": st.st_mtime}
            (dirs if p.is_dir() else files).append(item)
    except PermissionError:
        raise HTTPException(403, "That folder cannot be read.")
    for d in dirs:
        d["count"] = _count(here / d["name"])
    return {"dirs": dirs, "files": files}


def _count(p: Path) -> int:
    try:
        return sum(1 for _ in p.iterdir())
    except OSError:
        return 0


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def backup_status() -> dict:
    """What files-backup.sh last reported. See this module's header."""
    if not BACKUP_MANIFEST.exists():
        return {"ok": False,
                "why": "No backup has run yet, or its manifest is not mounted."}
    try:
        m = json.loads(BACKUP_MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"The backup manifest is unreadable: {e}"}
    age = time.time() - float(m.get("finished_at") or 0)
    days = age / 86400
    return {
        "ok": bool(m.get("vault_verified")) and days < 8,
        "why": ("Last backup " +
                (f"{age / 3600:.0f} hours" if days < 1 else f"{days:.1f} days")
                + " ago."),
        "when": m.get("finished_at_human", ""),
        "bytes": m.get("bytes", 0),
        "vault_verified": bool(m.get("vault_verified")),
        "trees": m.get("trees", []),
        "note": m.get("note", ""),
    }


def datetimeformat(ts) -> str:
    try:
        return time.strftime("%d %b %Y", time.localtime(float(ts)))
    except Exception:  # noqa: BLE001
        return ""


templates.env.globals["human"] = human
templates.env.filters["datetimeformat"] = datetimeformat


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    email = who(request)
    trees = trees_for(email)
    for t in trees:
        p = Path(t["root"])
        t["items"] = _count(p)
    # STARLETTE 1.x SIGNATURE: (request, name, context). The old
    # TemplateResponse(name, context) form was removed in 1.0, and this is a
    # new image so pip resolved 1.6 while the older apps here are still on
    # 0.41. Passing the context dict first made Jinja look up a template
    # named by a dict -- "unhashable type: 'dict'", which does not read as a
    # signature change at all.
    return templates.TemplateResponse(request, "index.html", { "root": ROOT_PATH, "site_name": SITE_NAME,
        "email": email, "trees": trees, "backup": backup_status(),
        "known": email in OWNERS,
    })


@app.get("/browse/{key}", response_class=HTMLResponse)
def browse(request: Request, key: str, path: str = ""):
    email = who(request)
    tree = tree_by_key(email, key)
    base = Path(tree["root"])
    data = _listing(base, path)
    crumbs, acc = [], ""
    for part in [p for p in path.split("/") if p]:
        acc = f"{acc}/{part}".lstrip("/")
        crumbs.append({"name": part, "rel": acc})
    return templates.TemplateResponse(request, "browse.html", { "root": ROOT_PATH, "site_name": SITE_NAME,
        "email": email, "tree": tree, "path": path, "crumbs": crumbs,
        **data,
    })


@app.get("/get/{key}")
def get_file(request: Request, key: str, path: str):
    email = who(request)
    tree = tree_by_key(email, key)
    target = _safe(Path(tree["root"]), path)
    if not target.is_file():
        raise HTTPException(404, "No such file.")
    media, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        target, media_type=media or "application/octet-stream",
        filename=target.name,
        # Downloaded, never rendered in place. These are other people's
        # documents and recordings; an HTML or SVG file among them would
        # otherwise run as script on this origin, with the session cookie
        # of whoever opened it.
        headers={"Content-Disposition": f'attachment; filename="{target.name}"',
                 "X-Content-Type-Options": "nosniff"})


@app.get("/api/backup")
def api_backup(request: Request):
    who(request)
    return JSONResponse(backup_status())


@app.get("/healthz")
def healthz():
    return {"ok": True}
