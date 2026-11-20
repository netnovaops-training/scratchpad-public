#!/usr/bin/env python3
"""aimode_pdf.py - a Google AI Mode chat link -> ONE long PDF page (no page breaks), on the PC.

    python D:/scratchpad/tools/aimode_pdf.py https://share.google/aimode/XXXX
    python D:/scratchpad/tools/aimode_pdf.py --clipboard            (the link you copied)
    python D:/scratchpad/tools/aimode_pdf.py --browser brave URL    (Brave instead of Chrome)
    python D:/scratchpad/tools/aimode_pdf.py --setup                (open the window once to sign in to Google)
    python D:/scratchpad/tools/aimode_pdf.py --html FILE.html       (an HTML export of the extension -> long PDF)
    python D:/scratchpad/tools/aimode_pdf.py --watch                (keep converting new exports in Downloads)

WHY (Jason 2026-11-19): printing an AI Mode page to PDF cuts lines at every A4 page edge and the search bar
covers the text. He wants one continuous PDF, from a link, without clicking through the extension by hand.
The NUC cannot open these links (Google shows servers a robot check), so this runs on the PC in a real
browser window.

HOW
1. Opens the link in a SEPARATE browser profile (D:/Tools/aimode-browser/<browser>-profile), never Jason's
   everyday profile (Chrome refuses automation of the default profile since 136, and this keeps it apart).
   The window opens at a fixed place and size (--x --y --w --h). If Google shows a robot check, the
   window waits for Jason to solve it (up to 5 minutes); this script never solves one itself.
2. Reads the chat with the code of the "AI Mode Chat Exporter" extension 1.3.1 (MIT, Neel K), the copy
   audited 2026-11-19 at D:/Tools/extensions/ai-mode-chat-exporter-1.3.1 (no network calls in it).
   The extension is not loaded: Chrome 137+ ignores --load-extension, so its two files are run directly.
   The files are checked against SHA256SUMS-audited.txt first; a changed file stops the run.
3. Lays the chat out with the extension's own HTML (its "PDF" format), then prints it as ONE page as tall
   as the content (A4 width). PDF pages max out at 200 inches; a longer chat continues on a second
   200-inch page instead of being cut at A4 size.
4. Saves to All-Documents/PDF as "AI Mode - <title> - <date>.pdf" and prints the path.

FIRST RUN 2026-11-19 (link mode): Google showed its robot check even on the PC, in the fresh automated
profile; nobody solved it, the run timed out after 5 minutes. So link mode needs Jason in front of the
window (or a signed-in profile via --setup). The reliable way is the HTML mode: Jason exports the chat with
the extension in his own browser (Format: HTML), and --watch turns every new export into the long PDF.
No Google page is automated in that mode at all.

OUTCOME: see the T-item Review line in TODO-ALL.md (first real run by Jason).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import pathlib
import re
import subprocess
import sys

EXT = pathlib.Path("D:/Tools/extensions/ai-mode-chat-exporter-1.3.1")
HOME = pathlib.Path("D:/Tools/aimode-browser")
OUT = pathlib.Path("C:/Jasons New Site/www/ai-stack/All-Documents/PDF")
BROWSERS = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
}
MAX_PAGE_PX = 19000          # 200 in at 96 px/in is 19200; keep a little room
ROBOT = re.compile(r"unusual traffic|not a robot|recaptcha", re.IGNORECASE)


def audited(name: str) -> str:
    """Return an extension file's text, refusing it unless its sha256 matches the audited list."""
    sums = dict(reversed(line.split(" *", 1)) for line in
                (EXT / "SHA256SUMS-audited.txt").read_text().split("\n") if " *" in line)
    data = (EXT / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != sums.get(name):
        sys.exit(f"{EXT / name} changed since the 2026-11-19 audit; not running it.")
    return data.decode("utf-8")


def extractor_js() -> str:
    """content-script.js without its chrome.runtime listener (lines 1-9), as one async expression."""
    src = audited("content-script.js")
    body = src[src.index("async function extractGoogleAiModeChat"):]
    return "(async () => {\n" + body + "\nreturn await extractGoogleAiModeChat();\n})()"


def layout_js() -> str:
    """popup.js from toMarkdown on (pure functions; the DOM/button code above it is left out)."""
    src = audited("popup.js")
    body = src[src.index("function toMarkdown"):]
    return "(chat) => {\n" + body + "\nreturn toHtml(chat, {includeSources: true, includeMeta: true, dark: false});\n}"


# One continuous page: no @page margins (the padding gives the white border), no print hint.
LONG_PAGE_CSS = "@page{margin:0} main{padding:14mm 14mm 16mm !important} .print-hint{display:none !important}"


def clipboard() -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                         capture_output=True, text=True, check=False).stdout
    m = re.search(r"https?://\S+", out)
    if not m:
        sys.exit("No link on the clipboard.")
    return m.group(0)


def safe(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", s).strip(" .")
    return re.sub(r"\s+", " ", s)[:90] or "chat"


def long_pdf(ctx, html: str, title: str, out: pathlib.Path) -> tuple[pathlib.Path, int]:
    """Lay out the extension's HTML at A4 width and print it as one page as tall as the content."""
    doc = ctx.new_page()
    doc.set_content(html)
    doc.add_style_tag(content=LONG_PAGE_CSS)
    doc.emulate_media(media="print")
    doc.set_viewport_size({"width": 794, "height": 1123})          # A4 width at 96 px/in
    height = doc.evaluate("Math.ceil(document.documentElement.scrollHeight)") + 4
    out.mkdir(parents=True, exist_ok=True)
    name = f"AI Mode - {safe(title)} - {datetime.date.today()}.pdf"
    target, n = out / name, 2
    while target.exists():
        target = out / name.replace(".pdf", f" ({n}).pdf")
        n += 1
    doc.pdf(path=str(target), width="794px", height=f"{min(height, MAX_PAGE_PX)}px", print_background=True)
    doc.close()
    return target, height


def is_export(p: pathlib.Path) -> bool:
    """True for an HTML file written by the extension (its turn markup and print hint)."""
    try:
        t = p.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return False
    return 'class="turn ' in t and "print-hint" in t


def html_mode(a, files: list[pathlib.Path]) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=BROWSERS[a.browser], headless=True)
        ctx = b.new_context()
        for f in files:
            html = f.read_text(encoding="utf-8")
            m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
            title = re.sub(r"&\w+;", " ", m.group(1)) if m else f.stem
            target, height = long_pdf(ctx, html, title, a.out)
            print(f"Saved {target} ({height}px tall) from {f.name}")
        b.close()


def watch(a) -> None:
    """Every 5 s: new extension exports in Downloads -> long PDF; the HTML is then moved to Downloads/converted."""
    import time
    src = pathlib.Path.home() / "Downloads"
    done = src / "converted"
    print(f"Watching {src} for AI Mode HTML exports (Ctrl+C stops)")
    while True:
        new = [p for p in src.glob("*.html") if time.time() - p.stat().st_mtime > 3 and is_export(p)]
        if new:
            html_mode(a, new)
            done.mkdir(exist_ok=True)
            for p in new:
                p.replace(done / p.name)
        time.sleep(5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url", nargs="?")
    ap.add_argument("--clipboard", action="store_true")
    ap.add_argument("--setup", action="store_true", help="open the window to sign in; close it when done")
    ap.add_argument("--browser", choices=sorted(BROWSERS), default="chrome")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--w", type=int, default=1100)
    ap.add_argument("--h", type=int, default=1000)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--html", type=pathlib.Path, nargs="+")
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    if a.html:
        return html_mode(a, a.html)
    if a.watch:
        return watch(a)
    url = clipboard() if a.clipboard else a.url
    if not url and not a.setup:
        ap.error("give a link, --clipboard or --setup")
    if url and not re.match(r"https://(share\.google|(www\.)?google\.com)/", url):
        sys.exit("Only Google AI Mode / Search links (share.google, google.com).")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(HOME / f"{a.browser}-profile"), executable_path=BROWSERS[a.browser], headless=False,
            no_viewport=True, args=[f"--window-position={a.x},{a.y}", f"--window-size={a.w},{a.h}"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if a.setup:
            page.goto("https://accounts.google.com/")
            print("Sign in to Google in this window (optional), then close the window.")
            page.wait_for_event("close", timeout=0)
            return
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30000)
        if ROBOT.search(page.inner_text("body")[:3000]):
            print("Google shows a robot check: solve it in the window (5 minutes)...")
            page.wait_for_function("!/unusual traffic|not a robot/i.test(document.body.innerText.slice(0,3000))",
                                   timeout=300000)
            page.wait_for_load_state("networkidle", timeout=30000)
        chat = page.evaluate(extractor_js())
        if not chat.get("turns"):
            sys.exit("No AI Mode conversation found on that page.")

        blank = ctx.new_page()
        html = blank.evaluate(layout_js(), chat)
        blank.close()
        target, height = long_pdf(ctx, html, chat.get("title") or "chat", a.out)
        ctx.close()
    pages = 1 if height <= MAX_PAGE_PX else -(-height // MAX_PAGE_PX)
    print(f"Saved {target} ({len(chat['turns'])} turns, {pages} page{'s' if pages > 1 else ''}, {height}px tall)")


if __name__ == "__main__":
    main()
