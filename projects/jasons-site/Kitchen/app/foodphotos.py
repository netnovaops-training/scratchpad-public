"""One picture per Tandoor food, stored on Kitchen's own volume.

WHY THIS IS A SEPARATE MODULE FROM photos.py
photos.py is keyed by (draft_id, slot) -- a recipe draft's own short id,
minted by store.py, six to thirty-two hex characters. A food's id is
Tandoor's own integer id, and there is exactly one picture per food rather
than a slot per step -- different key shape, different cardinality, so
this gets its own folder and its own id check rather than bending
photos.py's to fit both. The actual safety rules below are copied on
purpose: never trust the caller's filename, check magic numbers not
headers, cap size while reading, write-then-rename.

WHY THE FILE LIVES HERE AND NOT IN TANDOOR
Tandoor's Food model (checked against its live OpenAPI schema) has no
image field and no extras-style free JSON bag the way Mealie's
IngredientFood did -- there is nowhere on Tandoor's side to even put a
picture REFERENCE, let alone the bytes. So both live here: the metadata
cache below (get/set/all_with_images) stands in for what used to be
mealie.py's food.extras.image, same shape and same reasoning as
toolimages.py's own cache for tools (which had an identical problem on
Mealie's side already, before Tandoor even entered the picture).

CAUGHT LIVE (2026-08-24): this module's own id check used to require a
Mealie-shaped UUID4, left over from before the Tandoor migration -- every
food lookup was failing with "That is not a food I know about." the
moment a route was pointed at Tandoor's own integer ids. Tandoor is the
only source of food ids now that Mealie is stopped, so the check now
requires plain digits instead.
"""
import json
import re
import shutil
from pathlib import Path

from . import core

FOOD_PHOTO_DIR = core.DATA_DIR / "food-photos"
FOOD_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

FOOD_IMAGE_FILE = core.DATA_DIR / "food-images.json"

MAX_BYTES = int(core.os.getenv("KITCHEN_MAX_PHOTO_MB", "12")) * 1024 * 1024

# Same list as photos.py's _MAGIC -- a phone or a search result produces one
# of these, and the drawn ones are always PNG (see keep_drawn below).
_MAGIC = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),          # RIFF....WEBP; the WEBP is checked below
]

_FOOD_ID = re.compile(r"^\d+$")
_ID_IN_URL = re.compile(r"/food-image/[^/]+/")


class FoodPhotoError(Exception):
    """The picture was refused. The message is shown to the person."""


# --------------------------------------------------------------------------
#  The metadata cache -- stands in for Tandoor's missing extras.image the
#  same way toolimages.py's own cache stands in for a field Mealie's
#  RecipeTool never had. Read fresh every call, same as that module, so
#  two processes never need to coordinate over it.
# --------------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads(FOOD_IMAGE_FILE.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    FOOD_IMAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FOOD_IMAGE_FILE.with_suffix(".part")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(FOOD_IMAGE_FILE)


def get(food_id: str) -> dict | None:
    """This food's saved picture info, or None if it has never been given
    one -- callers check THIS before ever spending a search or a draw."""
    return _load().get(str(food_id))


def set(food_id: str, name: str, url: str, source: str, alt: str = "") -> None:
    data = _load()
    data[str(food_id)] = {"name": name, "url": url, "source": source, "alt": alt}
    _save(data)


def all_with_images() -> dict:
    """The whole cache, for _get_icon_map()'s bulk read -- one file read
    instead of one per food."""
    return _load()




def has_picture(food_id) -> bool:
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
        d = _dir_for(food_id)
    except FoodPhotoError:
        return False
    return d.is_dir() and any(d.iterdir())


def _kind(head: bytes) -> str:
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    raise FoodPhotoError(
        "That does not look like a photo. JPEG, PNG, GIF and WebP work."
    )


def _dir_for(food_id: str) -> Path:
    # Mealie food ids are UUID4 -- anything else is not one, and is refused
    # rather than turned into a path (same reasoning as photos.py's
    # _dir_for, just a different id shape to match).
    if not _FOOD_ID.match(food_id or ""):
        raise FoodPhotoError("That is not a food I know about.")
    return FOOD_PHOTO_DIR / food_id


def save(food_id: str, data: bytes, drawn: bool = False) -> str:
    """Store one picture already fetched into memory (either downloaded by
    foodimages.download_image or drawn by art.draw) -- the size cap is
    already enforced by the caller reading it, this just re-checks the
    bytes it was actually handed are a real picture. Returns the filename
    to record in Mealie's extras.image.url.

    `drawn` gets its own filename prefix, same reason as photos.py's
    keep_drawn: never confuse an AI picture for the real photo it stands
    in for, so approving a later real photo cannot silently leave a stale
    drawing under the same name.
    """
    if len(data) > MAX_BYTES:
        raise FoodPhotoError(
            f"That picture is bigger than {MAX_BYTES // (1024 * 1024)}MB.")
    if drawn:
        if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
            raise FoodPhotoError("That is not a PNG.")
        ext = "png"
    else:
        ext = _kind(data[:16])

    folder = _dir_for(food_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = "drawn" if drawn else "photo"
    name = f"{stem}.{ext}"
    temp = folder / f".{stem}.part"
    try:
        temp.write_bytes(data)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    # One file per food, so a re-search or a re-draw cannot accumulate
    # orphans -- same rule as photos.py's save().
    for old in folder.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    temp.replace(folder / name)
    return name


def path(food_id: str, name: str):
    """The file for a recorded picture name, or None. Refuses anything odd."""
    if not re.fullmatch(r"(photo|drawn)\.(jpg|png|gif|webp)", name or ""):
        return None
    try:
        p = _dir_for(food_id) / name
    except FoodPhotoError:
        return None
    return p if p.is_file() else None


def forget(food_id: str):
    """Everything stored for one food."""
    try:
        shutil.rmtree(_dir_for(food_id), ignore_errors=True)
    except FoodPhotoError:
        pass


def carry_over(old_id, new_id) -> bool:
    """A picture found for `old_id` survives a merge into `new_id`.

    CAUGHT LIVE (2026-08-24): tandoor.merge_food()'s own PUT already
    reassigns every Tandoor-side reference correctly, but a food's
    picture never lived in Tandoor at all (see this module's own
    docstring on why) -- it is a SEPARATE cache, keyed by the id that
    just stopped existing. Running a real merge pass with no carry-over
    step silently orphaned 30 already-found pictures before this was
    caught. Never overwrites an existing picture on `new_id` -- if the
    survivor already has its own, that one wins and `old_id`'s photo
    files are simply left on disk, unused, not deleted."""
    old_id, new_id = str(old_id), str(new_id)
    if get(new_id):
        return False
    entry = get(old_id)
    if not entry:
        return False
    try:
        src = _dir_for(old_id)
        dst = _dir_for(new_id)
    except FoodPhotoError:
        return False
    if src.is_dir() and not dst.exists():
        shutil.move(str(src), str(dst))
    new_url = _ID_IN_URL.sub(lambda m: f"/food-image/{new_id}/", entry.get("url") or "")
    set(new_id, entry.get("name") or "", new_url, entry.get("source") or "internet", entry.get("alt") or "")
    return True
