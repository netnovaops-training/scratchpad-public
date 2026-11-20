"""captcha_patch.py - share.py: refuse to file a CAPTCHA / bot-check page as if it were the shared page (2026-11-18).

Probe 11-18: a Google AI Mode link (google.com/search?udm=50...) printed by gotenberg-web came back as Google's
reCAPTCHA page ("Our systems have detected unusual traffic"), HTTP 200, and share.py would have filed it.
We never solve or get around CAPTCHAs; the drop now says so and files nothing. Applies to every shared link.
Needs gemini_patch.py first (uses _pdf_text and _Skip). Idempotent (marker BOT_WALLS).
"""
import pathlib
import sys

p = pathlib.Path("share.py")
s = p.read_text()
if "BOT_WALLS" in s:
    sys.exit("already patched")
if "_pdf_text" not in s:
    sys.exit("run gemini_patch.py first")

const = '''
# Bot checks that answer 200 (probe 11-18: Google AI Mode link -> reCAPTCHA). Compared without spaces.
BOT_WALLS = ("unusualtrafficfromyourcomputer", "i'mnotarobot", "verifyyouarehuman", "checkingifthesiteconnectionissecure")
'''
anchor = "\n\ndef _is_gemini("
assert anchor in s
s = s.replace(anchor, "\n" + const + anchor, 1)

old = '''                    if _is_gemini(safe):
                        flat = _pdf_text(pdf)'''
new = '''                    flat = _pdf_text(pdf) if safe else ""
                    if safe and any(w in flat for w in BOT_WALLS) and len(flat) < 4000:
                        results.append({"name": link, "status": "refused",
                                        "why": "the site showed a robot check (CAPTCHA) instead of the page; "
                                               "save it as PDF on the phone and share the file instead"})
                        raise _Skip
                    if _is_gemini(safe):'''
assert old in s
s = s.replace(old, new, 1)
s = s.replace("is refused, not filed.", "is refused, not filed.\nCAPTCHA pages (captcha_patch.py, 11-18) are refused for every link, never solved.", 1)
p.write_text(s)
print("patched")
