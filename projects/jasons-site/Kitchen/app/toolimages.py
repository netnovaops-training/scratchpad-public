"""Pictures for Mealie's Tools organizer (Dutch oven, stand mixer, oven...),
stored entirely on Kitchen's side.

WHY THIS CANNOT USE mealie.py's extras TRICK
Checked directly against Mealie's own OpenAPI schema: RecipeTool/
RecipeToolOut has NO extras field at all -- its only properties are id,
groupId, name, slug, householdsWithTool. Foods have somewhere to put a
picture reference because IngredientFood happens to have an extras bag;
tools have nowhere on the Mealie side, period. So the picture reference
itself -- not just the bytes -- has to live here, in a small JSON file
keyed by the tool's Mealie id. This file IS the single source of truth for
a tool's picture; nothing else needs to be checked or kept in sync with it.

THE DEDUPE RULE (Jason's own words: "if it finds a Large mixing bowl once
don't go find it again in another recipe.") For foods this falls out for
free -- one shared Mealie food record, one extras.image, read by every
recipe that uses it. Tools have no such shared field, so this cache is
what makes the same rule true here: main.py's tool-picture routes MUST
check get() before ever calling foodimages.search_images or art.draw, same
as mealie.py's _ensure_food_baseline looks its answer up once and never
again.

Deliberately a flat JSON file, not a table -- there is one row per tool
organizer in Jason's whole Mealie library (tens, not thousands), and this
matches the admin panel's own approved_file convention (ai-stack/rust/
admin's read_approved/write_approved) rather than growing a sqlite schema
for something this small.
"""
import json
import re
import shutil
from pathlib import Path

from . import core

TOOL_IMAGE_FILE = core.DATA_DIR / "tool-images.json"
TOOL_PHOTO_DIR = core.DATA_DIR / "tool-photos"
TOOL_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = int(core.os.getenv("KITCHEN_MAX_PHOTO_MB", "12")) * 1024 * 1024

_MAGIC = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),
]

# CAUGHT LIVE (2026-08-24): used to require a Mealie-shaped UUID4 --
# tools now live as Tandoor Keywords (see tandoor.py's TOOL_PREFIX
# convention), whose ids are plain integers, so every lookup here was
# failing with "not a tool I know about" the moment a caller passed one.
_TOOL_ID = re.compile(r"^\d+$")
_ID_IN_URL = re.compile(r"/tool-image/[^/]+/")


class ToolPhotoError(Exception):
    """The picture was refused. The message is shown to the person."""


# --------------------------------------------------------------------------
#  The metadata cache -- this file stands in for the extras field a tool
#  does not have. Read fresh every time, same as admin's approved_file, so
#  two processes never need to coordinate over it.
# --------------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads(TOOL_IMAGE_FILE.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    TOOL_IMAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = TOOL_IMAGE_FILE.with_suffix(".part")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(TOOL_IMAGE_FILE)


def get(tool_id: str) -> dict | None:
    """This tool's saved picture info, or None if it has never been given
    one -- callers check THIS before ever spending a search or a draw.

    CAUGHT LIVE (2026-08-24): this was missing the str() conversion
    foodphotos.py's own get() already has -- harmless while every caller
    happened to already be passing a string, but tandoor.all_tools()
    returns a real int id straight from Tandoor's JSON API, and an int
    key never matches a JSON-loaded cache's (always-string) keys. This is
    the actual reason /admin/items?kind=tool showed zero tools despite
    50 real pictures existing -- not anything the merge/dedupe work
    touched, confirmed by finding get(152) failed while get("152")
    worked, on a food that was never involved in any merge."""
    return _load().get(str(tool_id))


def set(tool_id: str, name: str, url: str, source: str, alt: str = "") -> None:
    data = _load()
    data[tool_id] = {"name": name, "url": url, "source": source, "alt": alt}
    _save(data)


def all_with_images() -> dict:
    """The whole cache, for the icon-map endpoint (see main.py's
    /icon-map) -- one lookup instead of one file read per tool."""
    return _load()


# --------------------------------------------------------------------------
#  The picture bytes -- same safety rules as foodphotos.py, a different id
#  shape and folder to match tools rather than foods.
# --------------------------------------------------------------------------



def has_picture(tool_id) -> bool:
    """Is there actually a file behind this entry's url?

    CAUGHT LIVE (2026-09-06): 44 foods and 13 tools had a cache entry
    pointing at a folder with nothing in it -- ginger, strawberries,
    bananas among them. Each rendered as a broken image, and worse, the
    cache is keyed by id while the icon map is keyed by NAME, so where two
    ids carried the same name the empty one could land second and displace
    the one that had the picture. Cheap stat, called once per cache
    rebuild, not per page.
    """
    try:
        d = _dir_for(tool_id)
    except ToolPhotoError:
        return False
    return d.is_dir() and any(d.iterdir())


def _kind(head: bytes) -> str:
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    raise ToolPhotoError("That does not look like a photo. JPEG, PNG, GIF and WebP work.")


def _dir_for(tool_id) -> Path:
    tool_id = str(tool_id) if tool_id is not None else ""
    if not _TOOL_ID.match(tool_id):
        raise ToolPhotoError("That is not a tool I know about.")
    return TOOL_PHOTO_DIR / tool_id


def save(tool_id: str, data: bytes, drawn: bool = False) -> str:
    """Store one picture already fetched into memory. Returns the filename
    to record in the cache above. Same drawn-prefix reasoning as
    foodphotos.save: never let an AI picture and a real one share a name."""
    if len(data) > MAX_BYTES:
        raise ToolPhotoError(f"That picture is bigger than {MAX_BYTES // (1024 * 1024)}MB.")
    if drawn:
        if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
            raise ToolPhotoError("That is not a PNG.")
        ext = "png"
    else:
        ext = _kind(data[:16])

    folder = _dir_for(tool_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = "drawn" if drawn else "photo"
    name = f"{stem}.{ext}"
    temp = folder / f".{stem}.part"
    try:
        temp.write_bytes(data)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    for old in folder.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    temp.replace(folder / name)
    return name


def path(tool_id: str, name: str):
    if not re.fullmatch(r"(photo|drawn)\.(jpg|png|gif|webp)", name or ""):
        return None
    try:
        p = _dir_for(tool_id) / name
    except ToolPhotoError:
        return None
    return p if p.is_file() else None


def forget(tool_id: str):
    try:
        shutil.rmtree(_dir_for(tool_id), ignore_errors=True)
    except ToolPhotoError:
        pass
    data = _load()
    if data.pop(tool_id, None) is not None:
        _save(data)


def carry_over(old_id, new_id) -> bool:
    """A picture found for `old_id` survives a merge into `new_id`. See
    foodphotos.py's own carry_over() -- same bug, same fix, the other
    kind. Never overwrites an existing picture on `new_id`."""
    old_id, new_id = str(old_id), str(new_id)
    if get(new_id):
        return False
    entry = get(old_id)
    if not entry:
        return False
    try:
        src = _dir_for(old_id)
        dst = _dir_for(new_id)
    except ToolPhotoError:
        return False
    if src.is_dir() and not dst.exists():
        shutil.move(str(src), str(dst))
    new_url = _ID_IN_URL.sub(lambda m: f"/tool-image/{new_id}/", entry.get("url") or "")
    set(new_id, entry.get("name") or "", new_url, entry.get("source") or "internet", entry.get("alt") or "")
    return True
