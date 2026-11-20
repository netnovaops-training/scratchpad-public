"""The collage: photographs of a wall, in the order they hang on it.

WHAT THIS IS FOR. There is a real collage on a real wall, above a fridge, and
a video was taken panning across it. The video is not content -- it is the
MAP. It says which picture sits where, and the job of this site is to put the
close-up photographs into that same order so somebody scrolling a phone walks
the wall in the order it actually hangs.

THE ORDER IS THE WHOLE POINT, which is why it is data and not a filename
convention. Renaming files to c001, c002 to reorder them is the kind of thing
that works until you need to insert one in the middle at eleven at night on a
phone, standing in front of the wall. order.json is a list; moving a photo is
moving one item in it.

WRITES ARE GATED BY THE HOUSEHOLD SSO, READS ARE NOT. Caddy puts forward_auth
in front of /manage and /api/* and passes the verified address in
X-Auth-Email; the gallery itself is public, because it is a wall of family
photographs and there is nobody to hide it from. This service TRUSTS that
header, which is only safe because it is unpublished -- nothing reaches it
except through Caddy. That is the same header-trust pattern Gitea, Paperless
and the Casey portal use here, and the same caveat applies: do not publish a
port to it.

STDLIB PLUS PILLOW, deliberately. This is one list, some files and a page. A
framework would be more to keep alive than the thing it serves, and the
garden API next door already proved the shape.
"""
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import watermark
from PIL import Image, ImageOps

ROOT = Path(os.environ.get("COLLAGE_ROOT", "/data"))
PHOTOS = ROOT / "photos"
THUMBS = ROOT / "thumbs"
ORDER = ROOT / "order.json"
STATIC = Path(os.environ.get("COLLAGE_STATIC", "/app/static"))
PORT = int(os.environ.get("PORT", "8024"))

# Who may rearrange the wall. Empty means "anybody the SSO let through",
# which is the household -- named here so it can be narrowed without a code
# change if that ever stops being the right answer.
EDITORS = {e.strip().lower() for e in
           os.environ.get("COLLAGE_EDITORS", "").split(",") if e.strip()}

# 4000px on the long edge. A phone camera gives 4-12MP and the full file is
# several megabytes; nobody scrolling a wall of photographs needs the sensor's
# full output, and on a phone connection it is the difference between a page
# that loads and one that does not.
MAX_EDGE = 4000
THUMB_EDGE = 900
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\Z")  # \Z, not $: $ lets a trailing newline through


def _child(base, name):
    """base/name as a real path, or None if it would land outside base.

    2026-11-20: every file the web routes touch goes through here, so a
    ../ or a symlink in a name can never reach outside its own folder.
    """
    base_real = os.path.realpath(str(base))
    full = os.path.realpath(os.path.join(base_real, str(name)))
    if full == base_real or not full.startswith(base_real.rstrip(os.sep) + os.sep):
        return None
    return Path(full)


for d in (PHOTOS, THUMBS):
    d.mkdir(parents=True, exist_ok=True)


def load_order():
    """The list, filtered to files that actually exist.

    FILTERED ON EVERY READ rather than cleaned on delete: a photo removed from
    disk by hand should disappear from the wall, not leave a broken frame, and
    an order file that has drifted should heal itself rather than need a
    repair tool.
    """
    try:
        listed = json.loads(ORDER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        listed = []
    have = {p.name for p in PHOTOS.iterdir() if p.is_file()}
    out = [n for n in listed if n in have]
    # Anything on disk but not in the list goes on the END. A photo that
    # appears from nowhere should be visible and out of the way, not silently
    # invisible because somebody copied it in rather than uploading it.
    out += sorted(have - set(out))
    return out


def save_order(names):
    tmp = ORDER.with_suffix(".tmp")
    tmp.write_text(json.dumps(names, indent=1), encoding="utf-8")
    tmp.replace(ORDER)


def make_thumb(name):
    src, dst = PHOTOS / name, THUMBS / name
    try:
        with Image.open(src) as im:
            # EXIF rotation baked into the pixels. A phone photo carries its
            # orientation as metadata, and something downstream always ignores
            # it -- then the wall is sideways and nobody can see why.
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
            im.convert("RGB").save(dst, "JPEG", quality=86, optimize=True)
    except Exception:  # noqa: BLE001  thumbnail is optional; any decode failure serves the full photo
        # A thumbnail is an optimisation. If one cannot be made the full photo
        # is served instead, which is slower and correct.
        if dst.exists():
            dst.unlink()


def _header_safe(value) -> str:
    """A header value with CR and LF removed, so it cannot start a new header or a second response.

    2026-11-20: written as str.replace (the form CodeQL's py/http-response-splitting recognises);
    an earlier re.sub version did the same thing but stayed flagged.
    """
    return str(value).replace("\r", "").replace("\n", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "collage"

    # ---- plumbing --------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        # 2026-11-20: CR/LF stripped from every header value, so nothing a
        # caller supplied can start a new header or a second response.
        self.send_header("Content-Type", _header_safe(ctype))
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(_header_safe(k), _header_safe(v))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _editor(self):
        """The signed-in address, or None.

        Taken from the header Caddy sets after verifying the session. Never
        from the body, and never from a query string: both are things a
        caller writes.
        """
        who = (self.headers.get("X-Auth-Email") or "").strip().lower()
        if not who:
            return None
        if EDITORS and who not in EDITORS:
            return None
        return who

    def _file(self, base: Path, name: str, cache="public, max-age=31536000, immutable"):
        path = _child(base, name)
        if path is None or not path.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self._send(200, data, ctype, {"Cache-Control": cache})

    # ---- routes ----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        p = url.path

        if p in ("/", "/manage"):
            # ONE PAGE, TWO MODES. The manage door is the same HTML; what
            # differs is that Caddy has already required a sign-in to reach
            # it, so the page can show the arrows. Two pages would be two
            # galleries to keep looking identical.
            return self._file(STATIC, "index.html", cache="no-cache")
        if p == "/healthz":
            return self._send(200, {"ok": True, "photos": len(load_order())})
        if p == "/api/photos":
            order = load_order()
            return self._send(200, {
                "photos": [{"name": n,
                            "thumb": f"/thumb/{n}" if (THUMBS / n).exists()
                                     else f"/photo/{n}"} for n in order],
                "editor": self._editor(),
            }, extra={"Cache-Control": "no-cache"})
        if p.startswith("/photo/"):
            name = p[len("/photo/"):]
            return self._file(PHOTOS, name) if SAFE_NAME.match(name) \
                else self._send(400, {"error": "bad name"})
        if p.startswith("/thumb/"):
            name = p[len("/thumb/"):]
            return self._file(THUMBS, name) if SAFE_NAME.match(name) \
                else self._send(400, {"error": "bad name"})
        if p.startswith("/static/"):
            name = p[len("/static/"):]
            return self._file(STATIC, name, cache="no-cache") \
                if SAFE_NAME.match(name) else self._send(400, {"error": "bad name"})
        self._send(404, {"error": "no such route"})

    def do_POST(self):
        who = self._editor()
        if not who:
            # Should be unreachable: Caddy gates these paths. Checked anyway,
            # because "the proxy will have handled it" is how a service ends
            # up writable the day somebody changes the proxy.
            return self._send(403, {"error": "sign in to change the wall"})

        url = urlparse(self.path)
        if url.path == "/api/move":
            return self._move(who)
        if url.path == "/api/upload":
            return self._upload(who)
        if url.path == "/api/delete":
            return self._delete(who)
        self._send(404, {"error": "no such route"})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _move(self, who):
        try:
            req = json.loads(self._body() or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        name, delta = req.get("name"), int(req.get("delta") or 0)
        order = load_order()
        if name not in order or delta == 0:
            return self._send(400, {"error": "nothing to move"})
        i = order.index(name)
        j = max(0, min(len(order) - 1, i + delta))
        if i == j:
            return self._send(200, {"photos": order})
        order.insert(j, order.pop(i))
        save_order(order)
        print(f"{who} moved {name} {i} -> {j}", flush=True)
        self._send(200, {"photos": order})

    def _upload(self, who):
        """One photo, sent as a raw body with the filename in a header.

        NOT multipart. The only client is the page next door, parsing
        multipart in the stdlib is more code than the feature, and a raw body
        with two headers is something curl can also do when something has gone
        wrong at eleven at night.
        """
        raw = self.headers.get("X-Filename") or "photo.jpg"
        ext = Path(raw).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            return self._send(400, {"error": f"{ext or 'that'} is not a photo"})
        data = self._body()
        if not data:
            return self._send(400, {"error": "empty upload"})

        # A generated name, not theirs. Two photos called IMG_1234.jpg from
        # two phones is the ordinary case, not the unlucky one.
        name = f"{int(time.time())}-{uuid.uuid4().hex[:6]}{ext}"
        dst = PHOTOS / name
        dst.write_bytes(data)

        cleaned = None
        try:
            with Image.open(dst) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")

                # THE GEMINI SPARKLE, TAKEN OFF BEFORE THE RESIZE. At full
                # resolution the glyph is larger, its edges are sharper
                # against the background, and the ring of pixels the fill
                # samples from has more in it. Cleaning a downscaled copy
                # would be removing a blurrier mark with less to rebuild
                # from, for nothing gained.
                #
                # WRAPPED, BECAUSE THIS MUST NEVER FAIL AN UPLOAD. Somebody
                # putting a picture on the fridge did not ask for watermark
                # removal and should not get an error about it -- if the
                # detector trips over something, the photo goes up as it is.
                try:
                    im, box, score = watermark.clean(im)
                    if box:
                        cleaned = {"box": list(box), "score": round(score, 3)}
                except Exception as wm_err:  # noqa: BLE001
                    print(f"watermark pass skipped: {wm_err}", flush=True)

                if max(im.size) > MAX_EDGE:
                    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
                im.save(dst, "JPEG", quality=90, optimize=True)
        except Exception as e:  # noqa: BLE001  any Pillow failure on an upload becomes a 400 for the user
            dst.unlink(missing_ok=True)
            return self._send(400, {"error": f"could not read that image: {e}"})

        make_thumb(name)
        order = load_order()
        # NEW PHOTOS GO TO THE TOP. Somebody standing at the wall adding one
        # wants to see it and move it, and the bottom of a long scroll is the
        # one place they will not look.
        order = [name] + [n for n in order if n != name]
        save_order(order)
        if cleaned:
            print(f"{who} added {name} ({len(data)} bytes) -- removed a "
                  f"watermark at {cleaned['box']} (confidence "
                  f"{cleaned['score']})", flush=True)
        else:
            print(f"{who} added {name} ({len(data)} bytes)", flush=True)
        # Reported back so the page can SAY it happened. Silently altering
        # somebody's photo is the wrong default even when the alteration is
        # the one they wanted.
        self._send(200, {"name": name, "photos": order, "cleaned": cleaned})

    def _delete(self, who):
        try:
            req = json.loads(self._body() or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        name = req.get("name")
        if not name or not SAFE_NAME.match(name):
            return self._send(400, {"error": "bad name"})
        gone = ROOT / "removed"
        gone.mkdir(exist_ok=True)
        # MOVED, NOT DELETED. This is somebody's family wall and the undo for
        # a mis-tap should not be "find the original again".
        for d in (PHOTOS, THUMBS):
            src = _child(d, name)
            dest = _child(gone, f"{d.name}-{name}")
            if src is not None and dest is not None and src.exists():
                shutil.move(str(src), str(dest))
        save_order([n for n in load_order() if n != name])
        print(f"{who} removed {name} (kept in removed/)", flush=True)
        self._send(200, {"photos": load_order()})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    missing = [n for n in load_order() if not (THUMBS / n).exists()]
    for n in missing:
        make_thumb(n)
    if missing:
        print(f"built {len(missing)} thumbnail(s) at start", flush=True)
    print(f"collage on 0.0.0.0:{PORT}, {len(load_order())} photo(s)", flush=True)
    # Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # nosec B104
