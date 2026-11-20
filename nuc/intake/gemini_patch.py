"""gemini_patch.py - teach share.py about Gemini share links (2026-11-18). Run on the NUC in ~/nuc-homelab/intake.

Jason 11-18: "adding the atomic drop to grab the Gemini pages and build out the feature later to include other
links". Probe (gemini_probe.py) 11-18: a share id that does not exist (or was un-shared) does NOT error; Google
answers 200 with the Gemini home page, so share.py printed "Hi, I'm Gemini. Where should we start?" to PDF and
filed it as if it were the chat. This patch:
  - recognises g.co/gemini/share/... and gemini.google.com/share/... (_is_gemini)
  - waits 8s instead of 3s for those pages (the chat is drawn by JavaScript)
  - reads the PDF's text back (pypdf, already in the image) and refuses to file the home page
  - names the file "Gemini chat - <title or date>.pdf" so the classifier and Paperless can tell it apart
Idempotent: refuses to run twice (marker _is_gemini).
"""
import pathlib
import sys

p = pathlib.Path("share.py")
s = p.read_text()
if "_is_gemini" in s:
    sys.exit("already patched")

helpers = '''

GEMINI_HOSTS = ("gemini.google.com", "g.co")
# Text on Gemini's home page; a dead, private or mistyped share link lands there with HTTP 200 (probe 11-18).
GEMINI_HOME = "where should we start"


def _is_gemini(url: str | None) -> bool:
    """True for a Gemini chat share link (g.co/gemini/share/..., gemini.google.com/share/...)."""
    if not url:
        return False
    p = urllib.parse.urlsplit(url)
    return (p.hostname or "") in GEMINI_HOSTS and "/share/" in p.path


def _pdf_text(pdf: bytes) -> str:
    """Plain text of a PDF, letters only spaced as printed (Gemini's font spaces every letter: 'H i ,  I 'm')."""
    import io
    import pypdf
    raw = " ".join((pg.extract_text() or "") for pg in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    return re.sub(r"\\s+", "", raw).lower()
'''
anchor = "\n\ndef _slug("
assert anchor in s, "anchor _slug not found"
s = s.replace(anchor, helpers + anchor, 1)

old_post = '''files={"url": (None, url), "waitDelay": (None, "3s")})'''
new_post = '''files={"url": (None, url), "waitDelay": (None, "8s" if _is_gemini(url) else "3s")})'''
assert old_post in s, "gotenberg call not found"
s = s.replace(old_post, new_post, 1)

old_file = '''                    p = tmp / name
                    p.write_bytes(pdf)
                    results.append(await _file(p, name, email))'''
new_file = '''                    if _is_gemini(safe):
                        flat = _pdf_text(pdf)
                        if GEMINI_HOME.replace(" ", "") in flat and len(flat) < 2000:
                            results.append({"name": link, "status": "refused",
                                            "why": "Gemini showed its home page: this chat is not shared "
                                                   "publicly, was un-shared, or the link is cut off"})
                            raise _Skip
                        import datetime
                        name = "Gemini chat - " + _slug(title, datetime.date.today().isoformat()) + ".pdf"
                    p = tmp / name
                    p.write_bytes(pdf)
                    results.append(await _file(p, name, email))
                except _Skip:
                    pass'''
assert old_file in s, "filing block not found"
s = s.replace(old_file, new_file, 1)

s = s.replace("\n\n@router.post(\"/share\")", "\n\nclass _Skip(Exception):\n    \"\"\"Stop filing this shared item; the reason is already in results.\"\"\"\n\n\n@router.post(\"/share\")", 1)
assert "class _Skip" in s

s = s.replace('OUTCOME: (2026-11-18) first version, T0872. See the T0872 Review lines for the tests.',
              'OUTCOME: (2026-11-18) first version, T0872. See the T0872 Review lines for the tests.\n'
              'Gemini (2026-11-18, gemini_patch.py): share links wait 8s, are named "Gemini chat - ...", and a link that\n'
              'only shows Gemini\'s home page (dead/private/cut-off share; Google answers 200) is refused, not filed.', 1)
p.write_text(s)
print("patched")
