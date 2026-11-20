"""The DJ book: nights worked, and the photographs from them.

WHAT THIS IS FOR. Jason DJs. The portfolio says so in one paragraph, which
is a claim; this is the evidence. A night gets a card -- where, when, what
it was -- and the photographs from that night sit under it. Recruiters
looking at the AV half of the CV land here; so does anybody who wants to
book him.

PHOTOGRAPHS ARRIVE FROM A PHONE, WHICH IS THE ONLY REQUIREMENT THAT SHAPED
ANYTHING. He is not at a desk when he has them -- he is in a car park at one
in the morning with a phone full of pictures. So: upload is a file input, it
takes many at once, it survives a bad signal one photo at a time rather than
failing the batch, and HEIC works because that is what an iPhone hands you.

WRITES ARE GATED BY THE HOUSEHOLD SSO, READS ARE NOT. Caddy puts
forward_auth in front of /manage and /api/* that writes, and passes the
verified address in X-Auth-Email. The gallery is public because the whole
point is showing it to somebody. This service TRUSTS that header, which is
only safe because it publishes no port -- nothing reaches it except through
Caddy. Same pattern as collage and Paperless here, same rule: do not publish
one.

ONE JSON FILE IS THE DATABASE. Two dozen nights and a few hundred
photographs is not a database problem, and a file that can be read with
`cat` when something is wrong at 1am is worth more here than indexes.

STDLIB PLUS PILLOW, matching collage next door. A framework would be more to
keep alive than the thing it serves.
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

from PIL import Image, ImageOps

# AN IPHONE HANDS YOU HEIC AND NOTHING ELSE READS IT. Without this the
# upload path accepts the file, Pillow refuses it, and the photographer gets
# "could not read that image" for a perfectly ordinary photo. Registered at
# import so every Image.open below simply works.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC = True
except Exception:  # noqa: BLE001
    HEIC = False

ROOT = Path(os.environ.get("DJ_ROOT", "/data"))
PHOTOS = ROOT / "photos"
THUMBS = ROOT / "thumbs"
LIBRARY = ROOT / "library.json"
STATIC = Path(os.environ.get("DJ_STATIC", "/app/static"))
PORT = int(os.environ.get("PORT", "8026"))

# Who may add a night. Empty means "anybody the SSO let through", which is
# the household -- named here so it can be narrowed without a code change.
EDITORS = {e.strip().lower() for e in
           os.environ.get("DJ_EDITORS", "").split(",") if e.strip()}

MAX_EDGE = 2600          # a gig photo is looked at, not printed
THUMB_EDGE = 800
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

SAFE_ID = re.compile(r"^[a-z0-9-]{1,40}$")
OK_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")

for d in (PHOTOS, THUMBS):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  The library
# ---------------------------------------------------------------------------
def load():
    """Always returns a well-formed library, whatever is on disk.

    A half-written file after a power cut should cost the last edit, not the
    whole site. Anything unreadable is treated as empty and rebuilt from the
    photographs that are actually there -- see reconcile().
    """
    try:
        data = json.loads(LIBRARY.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError  # noqa: TRY004  caught just below with every other bad library file
    except Exception:  # noqa: BLE001
        data = {}
    data.setdefault("gigs", [])
    data.setdefault("photos", [])
    return data


def save(data):
    # Written beside and renamed, so a reader never sees half a file.
    tmp = LIBRARY.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(LIBRARY)


def reconcile(data):
    """The files on disk are the truth; the list is an opinion about them.

    A photo in the list with no file is dropped. A file with no entry is
    added -- which is what makes `docker cp` a valid way to put a hundred
    photographs in at once without touching this service.
    """
    on_disk = {p.name for p in PHOTOS.iterdir() if p.is_file()}
    listed = {p["name"] for p in data["photos"]}
    data["photos"] = [p for p in data["photos"] if p["name"] in on_disk]
    for name in sorted(on_disk - listed, reverse=True):
        data["photos"].insert(0, {"name": name, "gig": "", "caption": ""})
    return data


def make_thumb(name):
    src, dst = PHOTOS / name, THUMBS / name
    try:
        with Image.open(src) as im:
            # EXIF rotation baked into the pixels. A phone photo carries its
            # orientation as metadata and something downstream always
            # ignores it -- then the whole night is sideways.
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
            im.convert("RGB").save(dst, "JPEG", quality=84, optimize=True)
    except Exception:  # noqa: BLE001
        # A thumbnail is an optimisation. Without one the full photo is
        # served: slower, and correct.
        if dst.exists():
            dst.unlink()


def slug(text, taken):
    """A readable id for a night, unique among the ones that exist."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:32]
    base = base or "night"
    s, n = base, 2
    while s in taken:
        s, n = f"{base}-{n}", n + 1
    return s


def _header_safe(value) -> str:
    """A header value with CR and LF removed, so it cannot start a new header or a second response.

    2026-11-20: written as str.replace (the form CodeQL's py/http-response-splitting recognises);
    an earlier re.sub version did the same thing but stayed flagged.
    """
    return str(value).replace("\r", "").replace("\n", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "dj"

    # ---- plumbing ---------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        # 2026-11-20 (CodeQL py/http-response-splitting, alert 3042): CR/LF
        # stripped from every header value, as in collage/app.py.
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
        from the body and never from a query string: both are things a caller
        writes for themselves.
        """
        who = (self.headers.get("X-Auth-Email") or "").strip().lower()
        if not who:
            return None
        if EDITORS and who not in EDITORS:
            return None
        return who

    def _file(self, base, name, cache="public, max-age=31536000, immutable"):
        path = _child(base, name)
        if path is None or not path.is_file():
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": cache})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self):
        try:
            return json.loads(self._body() or b"{}")
        except ValueError:
            return None

    # ---- reads ------------------------------------------------------------
    def do_GET(self):
        p = urlparse(self.path).path

        if p in ("/", "/manage"):
            # ONE PAGE, TWO MODES. The manage door is the same HTML; what
            # differs is that Caddy required a sign-in to reach it, so the
            # page shows the upload panel. Two pages would be two galleries
            # to keep looking identical.
            return self._file(STATIC, "index.html", cache="no-cache")
        if p == "/healthz":
            d = load()
            return self._send(200, {"ok": True, "gigs": len(d["gigs"]),
                                    "photos": len(d["photos"]), "heic": HEIC})
        if p == "/api/library":
            d = reconcile(load())
            return self._send(200, {
                "gigs": d["gigs"],
                "photos": [dict(p, thumb=(f"/thumb/{p['name']}"
                                          if (THUMBS / p["name"]).exists()
                                          else f"/photo/{p['name']}"))
                           for p in d["photos"]],
                "editor": self._editor(),
            }, extra={"Cache-Control": "no-cache"})
        for prefix, folder in (("/photo/", PHOTOS), ("/thumb/", THUMBS),
                               ("/static/", STATIC)):
            if p.startswith(prefix):
                name = p[len(prefix):]
                if not SAFE_NAME.match(name):
                    return self._send(400, {"error": "bad name"})
                cache = "no-cache" if folder is STATIC else \
                    "public, max-age=31536000, immutable"
                return self._file(folder, name, cache=cache)
        self._send(404, {"error": "no such route"})

    # ---- writes -----------------------------------------------------------
    def do_POST(self):
        who = self._editor()
        if not who:
            # Should be unreachable: Caddy gates these paths. Checked anyway,
            # because "the proxy will have handled it" is how a service ends
            # up writable the day somebody edits the proxy.
            return self._send(403, {"error": "sign in to change this"})

        p = urlparse(self.path).path
        route = {"/api/upload": self._upload, "/api/gig": self._gig,
                 "/api/assign": self._assign, "/api/move": self._move,
                 "/api/delete": self._delete,
                 "/api/gig-delete": self._gig_delete}.get(p)
        if not route:
            return self._send(404, {"error": "no such route"})
        route(who)

    def _upload(self, who):
        """One photo, raw body, filename and night in headers.

        NOT multipart, and not a batch. Parsing multipart in the stdlib is
        more code than the feature, and -- the reason that matters here --
        one request per photo means a dropped signal halfway through a phone
        full of pictures costs one picture, not the upload. The page sends
        them in sequence and shows the count going up.
        """
        raw = self.headers.get("X-Filename") or "photo.jpg"
        ext = Path(raw).suffix.lower()
        if ext not in OK_EXT:
            return self._send(400, {"error": f"{ext or 'that'} is not a photo"})
        gig = (self.headers.get("X-Gig") or "").strip().lower()
        if gig and not SAFE_ID.match(gig):
            return self._send(400, {"error": "bad night id"})
        data_in = self._body()
        if not data_in:
            return self._send(400, {"error": "empty upload"})

        # A generated name, not theirs. Two photos called IMG_1234.jpg from
        # two phones is the ordinary case, not the unlucky one. Always .jpg
        # because that is what gets written below, whatever arrived.
        name = f"{int(time.time())}-{uuid.uuid4().hex[:6]}.jpg"
        dst = PHOTOS / name
        try:
            tmp = PHOTOS / f".incoming-{name}"
            tmp.write_bytes(data_in)
            with Image.open(tmp) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                if max(im.size) > MAX_EDGE:
                    im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
                im.save(dst, "JPEG", quality=88, optimize=True)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            (PHOTOS / f".incoming-{name}").unlink(missing_ok=True)
            dst.unlink(missing_ok=True)
            hint = "" if HEIC or ext not in (".heic", ".heif") else \
                " (HEIC support is not installed in this image)"
            return self._send(400, {"error": f"could not read that image: {e}{hint}"})

        make_thumb(name)
        d = reconcile(load())
        d["photos"] = ([{"name": name, "gig": gig, "caption": ""}]
                       + [p for p in d["photos"] if p["name"] != name])
        save(d)
        print(f"{who} added {name} ({len(data_in)} bytes) to "
              f"{gig or 'no night'}", flush=True)
        self._send(200, {"name": name, "count": len(d["photos"])})

    def _gig(self, who):
        """Add or edit a night. An id is generated once and then kept.

        Renaming a night must not orphan its photographs, so the title is
        free to change and the id never does.
        """
        req = self._json()
        if req is None:
            return self._send(400, {"error": "bad json"})
        title = (req.get("title") or "").strip()
        if not title:
            return self._send(400, {"error": "a night needs a name"})
        d = load()
        gid = (req.get("id") or "").strip().lower()
        fields = {"title": title,
                  "venue": (req.get("venue") or "").strip(),
                  "date": (req.get("date") or "").strip(),
                  "note": (req.get("note") or "").strip()[:400]}
        if gid:
            if not SAFE_ID.match(gid):
                return self._send(400, {"error": "bad night id"})
            for g in d["gigs"]:
                if g["id"] == gid:
                    g.update(fields)
                    break
            else:
                return self._send(404, {"error": "no such night"})
        else:
            gid = slug(title, {g["id"] for g in d["gigs"]})
            d["gigs"].append(dict(fields, id=gid))
        # Newest night first, and nights with no date after the dated ones
        # rather than before them -- an empty string sorts above everything.
        d["gigs"].sort(key=lambda g: g.get("date") or "0000", reverse=True)
        save(d)
        print(f"{who} saved night {gid}: {title}", flush=True)
        self._send(200, {"id": gid, "gigs": d["gigs"]})

    def _assign(self, who):
        """Put a photo under a night, or give it a caption."""
        req = self._json()
        if req is None:
            return self._send(400, {"error": "bad json"})
        name = req.get("name") or ""
        if not SAFE_NAME.match(name):
            return self._send(400, {"error": "bad name"})
        d = load()
        for p in d["photos"]:
            if p["name"] == name:
                if "gig" in req:
                    gig = (req.get("gig") or "").strip().lower()
                    if gig and gig not in {g["id"] for g in d["gigs"]}:
                        return self._send(400, {"error": "no such night"})
                    p["gig"] = gig
                if "caption" in req:
                    p["caption"] = (req.get("caption") or "").strip()[:160]
                save(d)
                return self._send(200, {"ok": True})
        self._send(404, {"error": "no such photo"})

    def _move(self, who):
        req = self._json()
        if req is None:
            return self._send(400, {"error": "bad json"})
        name, delta = req.get("name"), int(req.get("delta") or 0)
        d = load()
        names = [p["name"] for p in d["photos"]]
        if name not in names or delta == 0:
            return self._send(400, {"error": "nothing to move"})
        i = names.index(name)
        j = max(0, min(len(names) - 1, i + delta))
        if i != j:
            d["photos"].insert(j, d["photos"].pop(i))
            save(d)
        self._send(200, {"ok": True})

    def _delete(self, who):
        req = self._json()
        if req is None:
            return self._send(400, {"error": "bad json"})
        name = req.get("name") or ""
        if not SAFE_NAME.match(name):
            return self._send(400, {"error": "bad name"})
        # MOVED, NOT DELETED. The undo for a mis-tap on a phone should not be
        # "find the original again".
        gone = ROOT / "removed"
        gone.mkdir(exist_ok=True)
        for folder in (PHOTOS, THUMBS):
            src = _child(folder, name)
            dest = _child(gone, f"{folder.name}-{name}")
            if src is not None and dest is not None and src.exists():
                shutil.move(str(src), str(dest))
        d = load()
        d["photos"] = [p for p in d["photos"] if p["name"] != name]
        save(d)
        print(f"{who} removed {name} (kept in removed/)", flush=True)
        self._send(200, {"ok": True})

    def _gig_delete(self, who):
        """Remove a night. Its photographs stay -- they just lose the label.

        Deleting a night should never delete somebody's pictures. If that is
        wanted, the photographs are still there to delete one at a time, and
        the accidental version of this is unrecoverable.
        """
        req = self._json()
        if req is None:
            return self._send(400, {"error": "bad json"})
        gid = (req.get("id") or "").strip().lower()
        d = load()
        d["gigs"] = [g for g in d["gigs"] if g["id"] != gid]
        for p in d["photos"]:
            if p.get("gig") == gid:
                p["gig"] = ""
        save(d)
        print(f"{who} removed night {gid}; its photos kept", flush=True)
        self._send(200, {"gigs": d["gigs"]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    lib = reconcile(load())
    save(lib)
    missing = [p["name"] for p in lib["photos"]
               if not (THUMBS / p["name"]).exists()]
    for n in missing:
        make_thumb(n)
    if missing:
        print(f"built {len(missing)} thumbnail(s) at start", flush=True)
    print(f"dj on 0.0.0.0:{PORT} -- {len(lib['gigs'])} night(s), "
          f"{len(lib['photos'])} photo(s), heic={HEIC}", flush=True)
    # Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # nosec B104
