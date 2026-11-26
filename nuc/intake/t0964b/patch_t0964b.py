#!/usr/bin/env python3
"""patch_t0964b.py - T0964 rest (classify.py and stale comments), in a COPY of intake only.

    python3 patch_t0964b.py ~/sandbox/t0964b/intake

classify.py
  - .docx (word/document.xml) and .odt (content.xml) now give their text: they were in DOCISH, but extract_text
    returned "" for them, so every Word/LibreOffice file was filed as a scan without the model reading it.
    The XML member is read only if it is under XML_MAX bytes (a zip bomb gives "" = the old behaviour), tags
    are stripped, the text is capped at `limit`. Only zipfile.BadZipFile / KeyError / OSError are caught.
  - .csv joins DOCISH: extract_text already read .csv, but the branch was never reached.
  - a document's "why" names its real destination when drop.toml sends documents somewhere other than Paperless.
  - quote_untrusted keeps tabs (as spaces): isprintable() dropped them, which glued tab-separated words together.
portal.py: MAX_GB shows at most one decimal, and a non-number INTAKE_MAX_MB falls back to 2048 instead of
  crashing the import.
Stale comments: scan.py ("every door ends in route_file" - /intake has its own check since T0962), share.py
  (app.upload -> app.route_file; Gotenberg is its own gotenberg-web), app.py ("checked below" -> at the top).
test_t0958.py: the no-op OCR_MAX_MB env line and the wrong "font-free path" note are gone.
Refuses on the live folder (by realpath) or if any old text is not found exactly once.
Variables: folder (the copy), texts (file name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser() if len(sys.argv) == 2 else None
if folder is None or not (folder / "classify.py").is_file():
    raise SystemExit("usage: patch_t0964b.py <copy of the intake folder>")
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
texts = {n: (folder / n).read_text(encoding="utf-8")
         for n in ("classify.py", "portal.py", "scan.py", "share.py", "app.py", "test_t0958.py")}
if "XML_MAX" in texts["classify.py"]:
    raise SystemExit("already patched")


def swap(name: str, old: str, new: str) -> None:
    if texts[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {texts[name].count(old)}")
    texts[name] = texts[name].replace(old, new)


swap("classify.py", 'DOCISH = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt"}\n',
     '''DOCISH = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".csv"}   # .csv: T0964
# T0964: the XML inside a .docx / .odt is read only up to this size (a zip bomb gives no text, as before).
XML_MAX = 20 * 1048576
OFFICE_XML = {".docx": "word/document.xml", ".odt": "content.xml"}
''')
swap("classify.py", '''    if ext == ".epub":
        try:
            with zipfile.ZipFile(path) as z:''', '''    if ext in OFFICE_XML:
        # T0964: Word / LibreOffice text. Before, these always came back "" and were filed as scans unread.
        try:
            with zipfile.ZipFile(path) as z:
                info = z.getinfo(OFFICE_XML[ext])
                if info.file_size > XML_MAX:
                    return ""   # implausibly big text part: treat like an unreadable file (the old outcome)
                xml = z.read(info).decode("utf-8", "replace")
            text = re.sub(r"<(w:p|text:p|text:h)\\b[^>]*>", "\\n", xml)   # paragraph starts -> line breaks
            text = re.sub(r"<[^>]+>", " ", text)
            return re.sub(r"[ \\t]+", " ", text).strip()[:limit]
        except (zipfile.BadZipFile, KeyError, OSError):   # not a real Office file: no text, filed as before
            return ""
    if ext == ".epub":
        try:
            with zipfile.ZipFile(path) as z:''')
swap("classify.py", '''        if kind == "document":
            # Into Paperless, which then applies the tag -> owner -> app
            # routing that already exists. This module does not duplicate it.
            why.append("Paperless decides its tag and owner from there")
''', '''        if kind == "document" and dest == "/srv/consume":
            # Into Paperless, which then applies the tag -> owner -> app
            # routing that already exists. This module does not duplicate it.
            why.append("Paperless decides its tag and owner from there")
        elif kind == "document":   # T0964: drop.toml sends documents elsewhere; say where
            why.append(f"filed to the document library ({dest})")
''')
swap("classify.py", '''        line = "".join(ch for ch in line if ch.isprintable())
''', '''        # T0964: a tab becomes a space (isprintable() dropped it and glued tab-separated words together)
        line = "".join(ch for ch in line.replace("\\t", " ") if ch.isprintable())
''')
swap("portal.py", '''MAX_GB = f"{int(os.environ.get('INTAKE_MAX_MB', '2048')) / 1024:g}"
''', '''def _max_gb() -> str:
    """T0964: the upload limit in GB, one decimal at most ("10", "1.5"); a non-number setting -> the 2048 default."""
    try:
        mb = int(os.environ.get("INTAKE_MAX_MB", "2048"))
    except ValueError:
        mb = 2048
    return f"{round(mb / 1024, 1):g}"


MAX_GB = _max_gb()
''')
swap("scan.py", '''/upload) ends in app.route_file(), so the check lives there, once.
''', '''/upload) ends in app.route_file(), which calls the check; /intake (Telegram + mail) has its own body
(_intake_sync) and calls the same check since T0962.
''')
swap("share.py", '''- Files: each one goes through the EXISTING /upload logic (app.upload), called as a function, so the
''', '''- Files: each one goes through the EXISTING upload logic (app.route_file), called as a function, so the
''')
swap("share.py", '''- A shared link (a Gemini share link, an article): Gotenberg (container `gotenberg`, already running for
  Paperless) prints the page to PDF with headless Chromium, then the PDF is filed like any upload.
''', '''- A shared link (a Gemini share link, an article): Gotenberg (its own container `gotenberg-web`, at
  GOTENBERG_URL, not Paperless's) prints the page to PDF with headless Chromium, then the PDF is filed like
  any upload.
''')
swap("app.py", 'AUTH_LEVELS_URL = os.environ.get("AUTH_LEVELS_URL", "")  # required (T0927 slice 2), checked below\n',
     'AUTH_LEVELS_URL = os.environ.get("AUTH_LEVELS_URL", "")  # required (T0927 slice 2), checked at the top\n')
swap("test_t0958.py", '''os.environ["OCR_MAX_MB"] = "0"
classify.OCR_MAX_MB = 0
''', '''classify.OCR_MAX_MB = 0   # classify reads OCR_MAX_MB at import, so the module value is what counts
''')
swap("test_t0958.py", '''Images are drawn with Tesseract's own font-free path: we write a PGM by hand (no Pillow in the image), with
the letters rendered by a tiny built-in 5x7 bitmap font, scaled up so Tesseract can read them.
''', '''The test draws its own images: a PGM written by hand (no Pillow in the image), the letters from a tiny
built-in 5x7 bitmap font scaled up; the real Tesseract then reads them.
''')
for name, text in texts.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: classify.py portal.py scan.py share.py app.py test_t0958.py")
