"""Load text documents into Paperless so Hermes can search them.

Run on the NUC (Paperless answers on 127.0.0.1 there via the LAN proxy).
Usage:  python3 paperless_load.py <folder> <source-tag> [--dry-run]

Each .txt / .md file under <folder> is uploaded once, titled by its relative
path, and tagged with <source-tag> (for example "api-docs" or "homelab-docs").
Markdown goes up as .txt because Paperless reads plain text, not Markdown.
Files whose title already exists are skipped, so re-running is safe.

The token is read from the Proton Pass login item below at run time and is
never printed or written to disk. Item made 2026-10-31 for the least-privilege
Paperless user "hermes-docs" (view + add documents/tags; no delete, no admin).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


BASE = os.environ.get("PAPERLESS_BASE", "http://192.168.1.86:8000")
VAULT = "API keys"
ITEM = "Paperless Hermes token (hermes-docs)"
MAX_BYTES = 900_000  # Paperless handles big text, but keep each upload modest
MAX_FILE_MB = int(os.environ.get("PAPERLESS_MAX_FILE_MB", "25"))  # bigger pdf/docx = a book, left to Kavita


def token() -> str:
    env = {**os.environ, "PROTON_PASS_KEY_PROVIDER": "fs"}
    r = subprocess.run(
        ["pass-cli", "item", "view", "--vault-name", VAULT, "--item-title", ITEM, "--field", "password"],
        env=env, capture_output=True, text=True, timeout=20,
        check=False,
    )
    t = r.stdout.strip()
    if r.returncode != 0 or not t:
        raise SystemExit("Could not read the Paperless token item from Proton Pass.")
    return t


def api(tok: str, path: str, data: bytes | None = None, ctype: str | None = None,
        method: str | None = None) -> dict:
    headers = {"Authorization": f"Token {tok}", "Accept": "application/json"}  # a pinned "; version=5" got HTTP 406 here 2026-10-31
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(_http_only(req), timeout=60) as resp:  # nosec B310
            body = resp.read().decode() or "{}"
            try:
                return json.loads(body)
            except ValueError:
                return {"raw": body[:200]}
    except urllib.error.HTTPError as e:
        # The reply body says what was wrong (Paperless validation messages; no secrets in them).
        raise RuntimeError(f"HTTP {e.code} on {path.split('?')[0]}: {e.read()[:200]!r}") from e


def ensure_tag(tok: str, name: str) -> int:
    found = api(tok, "/api/tags/?name__iexact=" + urllib.parse.quote(name))
    if found.get("results"):
        return found["results"][0]["id"]
    made = api(tok, "/api/tags/", json.dumps({"name": name}).encode(), "application/json")
    return made["id"]


def exists(tok: str, title: str) -> bool:
    found = api(tok, "/api/documents/?page_size=1&title__iexact=" + urllib.parse.quote(title))
    return bool(found.get("count"))


CTYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def original_date(p: Path) -> str | None:
    """The date the document was first made, from its own metadata, as YYYY-MM-DD.

    Jason, 2026-11-12: "use the meta data to find when it was made to find the original date".
    PDF: /CreationDate (D:YYYYMMDD...) or XMP xmp:CreateDate. Word: docProps/core.xml dcterms:created.
    Otherwise (txt/md, or metadata missing or nonsense) the file's modified time, which the moves
    and the tar sync to the NUC both kept. Years before 1985 or in the future are ignored
    (some PDF tools write 1970 or a blank clock).
    """
    import datetime
    import re
    import zipfile
    found = None
    try:
        if p.suffix.lower() == ".pdf":
            with open(p, "rb") as fh:
                head = fh.read(4 << 20)  # metadata is near the start or end; read both ends
                fh.seek(max(0, p.stat().st_size - (1 << 20)))
                head += fh.read()
            m = (re.search(rb"/CreationDate\s*\(D:(\d{4})(\d{2})(\d{2})", head)
                 or re.search(rb"<xmp:CreateDate>(\d{4})-(\d{2})-(\d{2})", head))
            if m:
                found = "-".join(x.decode() for x in m.groups())
        elif p.suffix.lower() in (".docx", ".pptx", ".xlsx"):  # all Office Open XML: same core.xml
            with zipfile.ZipFile(p) as z:
                core = z.read("docProps/core.xml").decode("utf-8", "replace")
            m = re.search(r"<dcterms:created[^>]*>(\d{4})-(\d{2})-(\d{2})", core)
            if m:
                found = "-".join(m.groups())
    except (OSError, KeyError, zipfile.BadZipFile, ValueError):
        found = None
    today = datetime.date.today()
    if found:
        try:
            d = datetime.date.fromisoformat(found)
            if 1985 <= d.year and d <= today:
                return found
        except ValueError:
            pass
    mtime = datetime.date.fromtimestamp(p.stat().st_mtime).isoformat()
    # The 2026-11-12 Google Drive download stamped its files with the download day, so for those
    # the modified time is not the original date: return None (Paperless then reads the date from
    # the text itself, and reorg.py names the file "undated").
    return None if mtime in NOT_ORIGINAL_DAYS else mtime


NOT_ORIGINAL_DAYS = {"2026-11-12"}


def upload(tok: str, title: str, text: str | bytes, tag_id: int | list[int], ext: str = ".txt",
           created: str | None = None) -> None:
    boundary = uuid.uuid4().hex
    parts = []

    def field(name: str, value: str) -> None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    field("title", title)
    if created:
        field("created", created)
    for t in (tag_id if isinstance(tag_id, list) else [tag_id]):
        field("tags", str(t))
    base = title.replace("/", "_").replace("\\", "_")
    fname = base if base.lower().endswith(ext) else base + ext
    body = text if isinstance(text, bytes) else text.encode("utf-8")
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{fname}"\r\n'
         f"Content-Type: {CTYPES.get(ext, 'text/plain')}\r\n\r\n").encode() + body + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    api(tok, "/api/documents/post_document/", b"".join(parts), f"multipart/form-data; boundary={boundary}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if len(args) != 2:
        raise SystemExit(__doc__)
    folder, source = Path(args[0]), args[1]
    # "~$name.docx" = Word's lock file left by an open document; Paperless answered HTTP 400 to
    # all 17 of them on 2026-11-12, so they are not sent.
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".txt", ".md", ".pdf", ".docx")
                   and p.is_file() and not p.name.startswith("~$"))
    tok = token()
    tag_id = None if dry else ensure_tag(tok, source)
    # --people (used by ~/load_all.sh for All-Documents, 2026-11-12): add the person tag, and send
    # personal documents with the ADMIN token so Jason owns them from the first second and the
    # hermes-docs account (Hermes) never sees them. Uploading as hermes-docs and locking afterwards
    # left a gap: Paperless consumes uploads in the background, so the later lock pass missed them.
    people = None
    if "--people" in sys.argv:
        import paperless_people as people  # same folder (repo) or ~ (NUC)
        admin = None if dry else people.admin_token()
        ptags = {} if dry else {n: ensure_tag(admin, n) for n in ("Jason", "Casey", "Mom", "private")}
    sent = skipped = failed = 0
    for p in files:
        title = f"{source}/{p.relative_to(folder).as_posix()}"
        use, tags = tok, tag_id
        if people and not dry:
            tags = [tag_id, ptags[people.person_of(title)]]
            if people.PRIVATE.search(title):
                use, tags = admin, tags + [ptags["private"]]
        ext = ".txt" if p.suffix.lower() == ".md" else p.suffix.lower()
        if ext == ".txt":
            data = p.read_text(encoding="utf-8", errors="replace")[:MAX_BYTES]
            if not data.strip():
                skipped += 1
                continue
        else:  # pdf / docx go up whole; Paperless reads and OCRs them itself
            # Jason 2026-11-12: "small doc and pdf into paperless and ebooks into the library".
            # Anything over MAX_FILE_MB is a book-sized file: it stays for Kavita, not Paperless.
            if p.stat().st_size > MAX_FILE_MB * 1024 * 1024:
                skipped += 1
                continue
            data = p.read_bytes()
        try:
            # the admin token sees everything, so a private file is never uploaded twice
            if exists(admin if people and not dry else tok, title):
                skipped += 1
            elif not dry:
                upload(use, title, data, tags, ext, original_date(p) if people else None)
                sent += 1
        except RuntimeError as e:
            failed += 1
            print("FAIL", title, e)
    print(f"{source}: {len(files)} files, uploaded {sent}, skipped {skipped}, failed {failed}" + (" (dry run)" if dry else ""))


if __name__ == "__main__":
    main()
