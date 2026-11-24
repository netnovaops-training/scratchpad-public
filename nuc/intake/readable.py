#!/usr/bin/env python3
"""Readable text of a shared web page: the article without menus, ads, scripts or cookie banners.

T0889 step 5 (2026-11-22, autopilot). Before this, every shared article was printed by Chromium as the
whole page (menus, cookie banner, ads, comments) to PDF. Now share.py fetches the page once (IP-pinned,
link_probe.fetch), extract() keeps the article text, and page_html() wraps it in a plain page that
Gotenberg prints. When extract() finds too little text (a JavaScript app such as Gemini, a login wall,
a bot check), share.py keeps the old Chromium print, so no link is dropped.

WHY THE STANDARD LIBRARY (html.parser) AND NOT trafilatura / readability-lxml
The intake image has no HTML parser and every new package needs a pip-audit/osv pass and a pinned
version (requirements.txt, T0931). A small extractor is enough for news and blog pages: take <article>,
else <main>, else <body>; drop script/style/nav/header/footer/aside/form and similar; keep headings,
paragraphs, lists, quotes and code. Answer line on T0889 (2026-11-22): recommended default, reversible.

SAFETY
The output holds ONLY tags from KEEP, with NO attributes, and every piece of text is html-escaped, so
nothing from the shared page (script, onload=, links, images, iframes) reaches Gotenberg's Chromium.
OUTCOMES: test_readable.py (offline) and the T0889 Review line of 2026-11-22.
"""
from __future__ import annotations

import datetime
import html
import html.parser
import re

# Containers whose whole contents are skipped.
DROP = {"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "embed", "canvas",
        "nav", "header", "footer", "aside", "form", "button", "select", "textarea", "dialog", "figure"}
# Block tags kept in the output (no attributes).
KEEP = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "blockquote", "pre", "code",
        "em", "strong", "b", "i", "br", "table", "tr", "td", "th"}
VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param",
        "track"}
MIN_CHARS = 800   # less than this = probably not an article (JS app, wall, error page): use Chromium


class _Parser(html.parser.HTMLParser):
    """Collect title and three candidate bodies (article, main, body) as lists of output pieces."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in_title = "", False
        self.skip = 0                                    # depth inside a DROP container
        self.zones = {"article": 0, "main": 0, "body": 0}
        self.out = {"article": [], "main": [], "body": []}

    def _emit(self, piece: str):
        for z, depth in self.zones.items():
            if depth:
                self.out[z].append(piece)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if self.skip or tag in DROP:
            if tag not in VOID:
                self.skip += 1
            return
        # role=navigation / banner / contentinfo and cookie/consent boxes are dropped like <nav>
        a = dict(attrs)
        marks = " ".join([a.get("role") or "", a.get("id") or "", a.get("class") or ""]).lower()
        if re.search(r"\b(navigation|banner|contentinfo|cookie|consent|newsletter|share|related|comment)",
                     marks) and tag not in VOID and tag not in self.zones:  # never drop article/main/body
            self.skip += 1
            return
        if tag in self.zones:
            self.zones[tag] += 1
        if tag in KEEP:
            self._emit(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.skip:
            if tag not in VOID:
                self.skip -= 1
            return
        if tag in KEEP and tag != "br":
            self._emit(f"</{tag}>")
        if tag in self.zones and self.zones[tag]:
            self.zones[tag] -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self.skip and data.strip():
            self._emit(html.escape(re.sub(r"\s+", " ", data)))


def _text_len(pieces: list[str]) -> int:
    return len(re.sub(r"<[^>]+>|\s+", "", html.unescape("".join(pieces))))


def extract(page: bytes | str) -> tuple[str, str, int]:
    """Return (title, body_html, text_chars) for a web page; body_html holds only KEEP tags, escaped text.

    <article> wins when it has MIN_CHARS of text, then <main>, then <body>.
    """
    if isinstance(page, bytes):
        m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", page[:4096], re.IGNORECASE)
        try:
            page = page.decode(m.group(1).decode() if m else "utf-8", errors="replace")
        except LookupError:
            page = page.decode("utf-8", errors="replace")
    p = _Parser()
    p.feed(page)
    p.close()
    for zone in ("article", "main", "body"):
        n = _text_len(p.out[zone])
        if n >= MIN_CHARS or zone == "body":
            return re.sub(r"\s+", " ", p.title).strip(), "".join(p.out[zone]), n
    return "", "", 0  # not reached


def page_html(title: str, body: str, url: str) -> str:
    """A plain printable page: title, source line (URL as text, not a link), the extracted body."""
    day = datetime.date.today().isoformat()
    return ("<!doctype html><meta charset=utf-8><title>{t}</title><style>body{{font:15px/1.6 Georgia,serif;"
            "margin:24px;max-width:720px}}small{{color:#555;font-family:sans-serif}}pre{{white-space:pre-wrap}}"
            "</style><h1>{t}</h1><p><small>Saved from {u} on {d}</small></p>{b}").format(
        t=html.escape(title), u=html.escape(url), d=day, b=body)
