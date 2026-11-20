"""Your own photographs, for the dish and for any step.

WHY THIS EXISTS ALONGSIDE THE SCRAPED ONES
------------------------------------------
crawl.py lifts what the source page had, and diagram.py draws the rest. Both
are automatic and neither is yours. But the useful picture is very often the
one you took: your pan, your oven, the point where the dough actually looked
right, the handwritten card the recipe came off.

So every slot on a guide can be overridden with a photo you upload, and an
uploaded photo beats both the scrape and the drawing. Nothing is ever
overwritten silently -- the scraped photo is still in the draft, and removing
yours brings it back.

WHY THE FILES LIVE HERE AND NOT IN MEALIE
-----------------------------------------
A draft is not in the library yet and may never be. Pushing photos to Mealie on
upload would mean creating a recipe there to hang them off, which is exactly
the "everything I ever imported is now in the library" mess that drafts exist
to avoid. They live on the kitchen volume, travel with the draft, and are
uploaded to Mealie at the moment you press Keep.

THE CHECKS, AND WHY EACH ONE IS HERE
------------------------------------
Uploads are the one place a signed-in person hands this container a file, so:

  * the extension comes from a fixed list, never from the upload's own name
  * the name on disk is derived from the draft id and slot, never from the
    filename -- so "../../etc/passwd" is not a filename this code can produce
  * the size is capped while READING, not from a header a client controls
  * the first bytes are checked against known magic numbers, so a .jpg that is
    actually a script is refused rather than stored and served back
"""
import re
import shutil
from pathlib import Path

from . import core

PHOTO_DIR = core.DATA_DIR / "photos"
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = int(core.os.getenv("KITCHEN_MAX_PHOTO_MB", "12")) * 1024 * 1024

# Extension by magic number. The KEY is what we trust; the uploaded filename is
# only ever used to guess which of these to check first, and even that is
# thrown away. A phone camera produces one of these five.
_MAGIC = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),          # RIFF....WEBP; the WEBP is checked below
]

# hero, or a step number. Anything else is not a slot.
_SLOT = re.compile(r"^(hero|step-\d{1,3})$")


class PhotoError(Exception):
    """The upload was refused. The message is shown to the person."""


def valid_slot(slot: str) -> bool:
    return bool(_SLOT.match(slot or ""))


def _kind(head: bytes) -> str:
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    raise PhotoError(
        "That does not look like a photo. JPEG, PNG, GIF and WebP work — "
        "which is what a phone camera produces."
    )


def _dir_for(draft_id: str) -> Path:
    # draft ids are 12 hex characters from uuid4; anything else is not one, and
    # is refused rather than being turned into a path.
    if not re.fullmatch(r"[0-9a-f]{6,32}", draft_id or ""):
        raise PhotoError("That is not a recipe I know about.")
    return PHOTO_DIR / draft_id


def save(draft_id: str, slot: str, upload) -> str:
    """Store one uploaded photo. Returns the filename to record on the draft."""
    if not valid_slot(slot):
        raise PhotoError("That is not a place a photo can go.")

    head = upload.file.read(16)
    ext = _kind(head)

    folder = _dir_for(draft_id)
    folder.mkdir(parents=True, exist_ok=True)

    # WRITTEN BESIDE THE OLD ONE, AND ONLY SWAPPED IN ONCE IT IS WHOLE.
    #
    # The obvious order -- delete the old photo, then write the new one --
    # loses the old photo whenever the new one is refused. A phone photograph
    # over the size cap is not an unusual event, it is a normal one, and the
    # person who hits it has just destroyed the picture they took last week to
    # replace it, with nothing on the page to say so. Writing to a temporary
    # name first means a refused upload changes nothing at all.
    name = f"{slot}.{ext}"
    temp = folder / f".{slot}.part"
    written = len(head)
    try:
        with open(temp, "wb") as out:
            out.write(head)
            while True:
                chunk = upload.file.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise PhotoError(
                        f"That photo is bigger than "
                        f"{MAX_BYTES // (1024 * 1024)}MB, so the one that was "
                        "already there has been left alone. Most phones let "
                        "you send a smaller copy.")
                out.write(chunk)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    # Only now is the old one gone. One file per slot, so a draft cannot
    # accumulate orphans -- and the glob is anchored at the start of the name,
    # which is what keeps it off drawn-step-N.png.
    for old in folder.glob(f"{slot}.*"):
        old.unlink(missing_ok=True)
    temp.replace(folder / name)
    return name


def keep_drawn(draft_id: str, slot: str, png: bytes) -> str:
    """Store a drawn picture. Same folder, a name that says what it is.

    The `drawn-` prefix is not decoration. It is what keeps a drawing out of
    the slot a photograph lives in, so uploading your own photo of a step does
    not have to delete the drawing first, removing your photo puts the drawing
    back rather than falling all the way to the diagram, and no code path can
    ever mistake one for the other.
    """
    if not valid_slot(slot):
        raise PhotoError("That is not a place a picture can go.")
    # The magic number, not the caller's word for it. The only writer is
    # art.py, but a check costing eight bytes is not worth trusting over.
    if not png.startswith(bytes.fromhex("89504e470d0a1a0a")):
        raise PhotoError("That is not a PNG.")
    if len(png) > MAX_BYTES:
        raise PhotoError("That picture is too big.")
    folder = _dir_for(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"drawn-{slot}.png"
    (folder / name).write_bytes(png)
    return name


def save_reference(draft_id: str, slot: str, image_bytes: bytes) -> str:
    """A picture Jason found online and wants held up as a reference for
    what a step (or the dish) should actually look like -- kept
    completely separate from `mine`/`drawn-`, same reasoning as
    keep_drawn()'s own prefix: a reference is neither a real photo of
    THIS cooking session nor an AI drawing, and no code path should ever
    show it as either. Never pushed to the library on its own -- it only
    ever exists to be DESCRIBED (see llm.describe_image) and folded into
    a drawing prompt, not displayed as if it were this recipe's own
    picture.
    """
    if not valid_slot(slot):
        raise PhotoError("That is not a place a picture can go.")
    head = image_bytes[:16]
    ext = _kind(head)
    if len(image_bytes) > MAX_BYTES:
        raise PhotoError("That picture is too big.")
    folder = _dir_for(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"ref-{slot}.*"):
        old.unlink(missing_ok=True)
    name = f"ref-{slot}.{ext}"
    (folder / name).write_bytes(image_bytes)
    return name


def path(draft_id: str, name: str):
    """The file for a recorded photo name, or None. Refuses anything odd."""
    if not re.fullmatch(r"(drawn-|ref-)?(hero|step-\d{1,3})\.(jpg|png|gif|webp)", name or ""):
        return None
    try:
        p = _dir_for(draft_id) / name
    except PhotoError:
        return None
    return p if p.is_file() else None


def remove(draft_id: str, slot: str, drawn: bool = False, reference: bool = False):
    if not valid_slot(slot):
        return
    stem = f"drawn-{slot}" if drawn else f"ref-{slot}" if reference else slot
    for old in _dir_for(draft_id).glob(f"{stem}.*"):
        old.unlink(missing_ok=True)


def forget(draft_id: str):
    """Everything for one draft. Called when a draft is swept."""
    try:
        shutil.rmtree(_dir_for(draft_id), ignore_errors=True)
    except PhotoError:
        pass
