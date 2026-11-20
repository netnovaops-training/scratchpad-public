"""Illustrated how-to guides.

WHY THE PICTURES ARE DRAWN BY CODE
----------------------------------
A language model cannot produce an accurate screenshot of Android or Windows.
Ask one for a picture of a Settings screen and you get invented labels, invented
layout, and buttons that do not exist. For someone who is not confident with
technology that is actively harmful: she hunts for a control that was never
there and concludes the fault is hers.

So nothing here is generated as an image by the model. The model writes the
STEPS as text; this module draws a deterministic SVG schematic from those steps
-- a device outline with the relevant region highlighted and numbered. It is
honest about being a diagram rather than pretending to be a photograph, and it
is correct by construction because code positions every element.

Where a real screenshot exists in the library (see `screenshot_for`), it is used
in preference to the schematic. That library grows as you capture real screens.
"""
import html
import re
from pathlib import Path
from typing import Optional

from . import core

SHOTS_DIR = Path(core.DATA_DIR) / "screenshots"

# --- working out what a step is talking about -------------------------------
# Keyword -> (device, region). Region drives where the highlight is drawn.
# Order matters: the most specific device wins. "Restart your internet box"
# mentions no phone, but a generic hint further down must not claim it first.
_DEVICE_HINTS = [
    (r"\b(router|modem|internet box|wi-?fi box|broadband box)\b", "router"),
    (r"\b(printer|scanner)\b", "printer"),
    (r"\b(phone|android|samsung|pixel|tablet|swipe)\b", "phone"),
    (r"\b(computer|laptop|pc|windows|desktop|taskbar|start button)\b", "window"),
]

_REGION_HINTS = [
    (r"\b(top right|upper right|three dots|three little dots|menu)\b", "top-right"),
    (r"\b(top left|upper left|back arrow)\b", "top-left"),
    (r"\b(bottom right)\b", "bottom-right"),
    (r"\b(bottom left|start button|start menu)\b", "bottom-left"),
    (r"\b(top|swipe down|notification|address bar|search bar)\b", "top"),
    (r"\b(bottom|home button|taskbar|dock)\b", "bottom"),
    (r"\b(middle|centre|center|list|scroll)\b", "middle"),
]

# Words that are never a control she needs to find on screen.
_STOP_LABELS = {
    "the", "you", "your", "tap", "click", "turn", "on", "off", "press", "open",
    "then", "now", "next", "wait", "and", "it", "this", "that", "when", "if",
    "unplug", "plug", "restart", "select", "choose", "go", "find", "look",
}

_ACTION_HINTS = [
    (r"\b(tap|touch|press)\b", "tap"),
    (r"\b(click|select|choose)\b", "click"),
    (r"\b(type|enter|write)\b", "type"),
    (r"\b(turn off|switch off|toggle|slide)\b", "toggle"),
    (r"\b(restart|reboot|turn it off and)\b", "power"),
    (r"\b(plug|unplug|cable|cord)\b", "cable"),
    (r"\b(wait|count to)\b", "wait"),
]


def analyse(step: str, category: str = "") -> dict:
    t = (step or "").lower()
    device = {"phone": "phone", "computer": "window", "printer": "printer",
              "internet": "router"}.get(category, "")
    for pat, dev in _DEVICE_HINTS:
        if re.search(pat, t):
            device = dev
            break
    device = device or "window"

    region = "middle"
    for pat, r in _REGION_HINTS:
        if re.search(pat, t):
            region = r
            break

    action = "tap" if device == "phone" else "click"
    for pat, a in _ACTION_HINTS:
        if re.search(pat, t):
            action = a
            break

    # What she has to find on screen. A quoted phrase is nearly always the
    # control name, so trust that first.
    label = ""
    m = re.search(r"[\"'\u201c]([^\"'\u201d]{2,28})[\"'\u201d]", step or "")
    if m:
        label = m.group(1)
    else:
        # Fall back to Title Case, but NOT the first word of the sentence --
        # "Turn off the switch" would otherwise label the diagram "Turn", which
        # is worse than no label at all.
        for m in re.finditer(r"\b([A-Z][a-z]+(?:-[A-Za-z]+)?(?: [A-Z][a-z]+){0,2})\b", step or ""):
            if m.start() == 0 and " " not in m.group(1):
                continue
            if m.group(1).lower() in _STOP_LABELS:
                continue
            label = m.group(1)
            break
    return {"device": device, "region": region, "action": action, "label": label[:24]}


# --- drawing -----------------------------------------------------------------

_REGION_BOX = {
    "top":          (0.10, 0.08, 0.80, 0.13),
    "top-left":     (0.06, 0.07, 0.34, 0.12),
    "top-right":    (0.60, 0.07, 0.34, 0.12),
    "middle":       (0.10, 0.40, 0.80, 0.16),
    "bottom":       (0.10, 0.78, 0.80, 0.13),
    "bottom-left":  (0.06, 0.80, 0.34, 0.12),
    "bottom-right": (0.60, 0.80, 0.34, 0.12),
}


def step_svg(step: str, number: int, category: str = "", width: int = 260,
             height: int = 300) -> str:
    """A schematic of where to look. Deliberately simple and unmistakably a
    diagram -- it must never be confused for a real screenshot."""
    a = analyse(step, category)
    e = html.escape
    W, H = width, height
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'width="{W}" height="{H}" role="img" '
             f'aria-label="Diagram for step {number}">']
    parts.append('<style>'
                 '.fr{fill:none;stroke:currentColor;stroke-width:2.5;opacity:.55}'
                 '.sc{fill:currentColor;opacity:.05}'
                 '.ln{fill:currentColor;opacity:.16}'
                 '.hl{fill:none;stroke:#c2410c;stroke-width:3.5;rx:7}'
                 '.hlf{fill:#c2410c;opacity:.13}'
                 '.tx{font:600 12px ui-sans-serif,system-ui,sans-serif;fill:#c2410c}'
                 '.num{font:800 15px ui-sans-serif,system-ui,sans-serif;fill:#fff}'
                 '.cap{font:500 11px ui-sans-serif,system-ui,sans-serif;'
                 'fill:currentColor;opacity:.6}'
                 '</style>')

    # device outline
    if a["device"] == "phone":
        bx, by, bw, bh = W * 0.24, 18, W * 0.52, H - 62
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="18" class="sc"/>')
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="18" class="fr"/>')
        parts.append(f'<rect x="{bx + bw * 0.36}" y="{by + 7}" width="{bw * 0.28}" '
                     f'height="4" rx="2" class="ln"/>')
        caption = "Your phone"
    elif a["device"] == "printer":
        bx, by, bw, bh = W * 0.12, H * 0.24, W * 0.76, H * 0.42
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="10" class="sc"/>')
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="10" class="fr"/>')
        parts.append(f'<rect x="{bx + bw * 0.15}" y="{by - 16}" width="{bw * 0.7}" '
                     f'height="16" rx="3" class="ln"/>')
        caption = "Your printer"
    elif a["device"] == "router":
        bx, by, bw, bh = W * 0.18, H * 0.36, W * 0.64, H * 0.26
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" class="sc"/>')
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" class="fr"/>')
        for i in range(3):
            parts.append(f'<circle cx="{bx + 18 + i * 16}" cy="{by + bh - 14}" r="3.5" class="ln"/>')
        caption = "Your internet box"
    else:
        bx, by, bw, bh = 14, 26, W - 28, H - 72
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" class="sc"/>')
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" class="fr"/>')
        parts.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="20" rx="8" class="ln"/>')
        for i in range(3):
            parts.append(f'<circle cx="{bx + 12 + i * 12}" cy="{by + 10}" r="3.2" '
                         f'fill="currentColor" opacity=".3"/>')
        caption = "Your computer"

    # highlight
    rx, ry, rw, rh = _REGION_BOX.get(a["region"], _REGION_BOX["middle"])
    hx, hy = bx + bw * rx, by + bh * ry
    hw, hh = bw * rw, bh * rh
    parts.append(f'<rect x="{hx}" y="{hy}" width="{hw}" height="{hh}" rx="7" class="hlf"/>')
    parts.append(f'<rect x="{hx}" y="{hy}" width="{hw}" height="{hh}" rx="7" class="hl"/>')

    if a["label"]:
        parts.append(f'<text x="{hx + hw / 2}" y="{hy + hh / 2 + 4}" class="tx" '
                     f'text-anchor="middle">{e(a["label"])}</text>')

    # action marker
    cx, cy = hx + hw / 2, hy + hh / 2
    if a["action"] in ("tap", "click"):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{max(hw, hh) * 0.55}" '
                     f'fill="none" stroke="#c2410c" stroke-width="2" opacity=".45"/>')
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{max(hw, hh) * 0.78}" '
                     f'fill="none" stroke="#c2410c" stroke-width="1.4" opacity=".22"/>')
    elif a["action"] == "power":
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="13" fill="none" stroke="#c2410c" '
                     f'stroke-width="3"/>')
        parts.append(f'<line x1="{cx}" y1="{cy - 15}" x2="{cx}" y2="{cy - 2}" '
                     f'stroke="#c2410c" stroke-width="3.5" stroke-linecap="round"/>')

    # step number badge
    parts.append('<circle cx="22" cy="22" r="15" fill="#c2410c"/>')
    parts.append(f'<text x="22" y="27" class="num" text-anchor="middle">{number}</text>')
    parts.append(f'<text x="{W / 2}" y="{H - 12}" class="cap" text-anchor="middle">'
                 f'{e(caption)} — where to look</text>')
    parts.append("</svg>")
    return "".join(parts)


# --- real screenshots, when they exist ---------------------------------------

def screenshot_for(step: str, category: str = "") -> Optional[str]:
    """Look for a real captured screenshot matching this step.

    Drop PNGs into <data>/screenshots/ named after what they show, using
    hyphens, e.g.:
        android-settings-connections.png
        windows-settings-printers.png
        windows-start-button.png
    The more you capture, the more steps get a real picture instead of a
    schematic. Matching is deliberately simple: the filename words are scored
    against the step text.
    """
    if not SHOTS_DIR.exists():
        return None
    words = set(re.findall(r"[a-z]{3,}", (step or "").lower()))
    if category:
        words.add(category)
    best, best_score = None, 0
    for f in SHOTS_DIR.glob("*.png"):
        parts = set(re.split(r"[-_ ]+", f.stem.lower()))
        score = len(words & parts)
        if score > best_score:
            best, best_score = f.name, score
    # Require at least two matching words, otherwise the picture is likely wrong,
    # and a wrong picture is worse than none.
    return best if best_score >= 2 else None


def guide_html(steps: list, category: str = "") -> str:
    """Render the illustrated steps as a fragment for the ticket/FAQ page."""
    if not steps:
        return ""
    out = ['<div class="guide">']
    for i, s in enumerate(steps, 1):
        shot = screenshot_for(s, category)
        pic = (f'<img src="{core.ROOT_PATH}/shot/{html.escape(shot)}" alt="" loading="lazy">'
               if shot else step_svg(s, i, category))
        out.append(f'<figure class="gstep">{pic}'
                   f'<figcaption><span class="n">{i}</span>{html.escape(str(s))}'
                   f'</figcaption></figure>')
    out.append("</div>")
    return "".join(out)
