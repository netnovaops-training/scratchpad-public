"""Drawn diagrams for the steps that have no photograph.

WHY THESE ARE DRAWN BY CODE
---------------------------
This is the same argument Mom's guides.py makes about screenshots, applied to
food. Ask a model for a picture of "fold the butter into the flour" and you get
something that looks like a photograph and is not one: the wrong bowl, the
wrong colour, a texture that is not what yours will look like. Someone
following the method compares their bowl to it and concludes they have done it
wrong. A wrong picture is worse than no picture.

So nothing here is generated. The model writes the STEP as text; this module
reads that text for what KIND of thing is happening and draws a schematic --
a pan on a hob, an oven with its temperature on it, a bowl, a board. It is
obviously a diagram rather than pretending to be a photograph, and it is
correct by construction, because code positions every line.

Where the source page had a real photograph for a step, guide.py uses that
instead and this module is never called. Real photograph first, drawing
second, invented image never.

WHAT THE DRAWING ACTUALLY ADDS
------------------------------
Two things worth having, both lifted from the step text rather than guessed:
the TEMPERATURE goes on the oven and the TIME goes on the timer. Scanning a
method for "180C" is exactly what you do when your hands are covered in flour,
and a number in a picture is faster to find than a number in a sentence.

Everything is one colour (currentColor) so the drawings follow the page into
dark mode without a second palette to keep in step.
"""
import html
import re

# Scene detection. ORDER MATTERS -- the most specific wins. "Bake at 180C for
# 20 minutes" mentions time, but it is an oven step, and the oven is the more
# useful drawing. A generic hint further down must not claim it first.
_SCENES = [
    # "baking tray" is a tray, not an oven -- the lookahead keeps the tray
    # drawing for a step that is only about lining and arranging.
    ("oven",   r"\b(oven|bake|baking(?!\s+(?:tray|sheet|dish|paper|parchment))"
               r"|roast|roasting|preheat|pre-heat|broil|grill\s+the)\b"),
    ("boil",   r"\b(boil|boiling|simmer|simmering|blanch|poach|stock|saucepan|pot\b)"),
    ("pan",    r"\b(fry|frying|saut[eé]|sear|brown\s+the|skillet|pan\b|griddle|hob|stovetop|stove)\b"),
    ("board",  r"\b(chop|dice|slice|mince|cut|julienne|shred|carve|trim|peel)\b"),
    ("bowl",   r"\b(mix|stir|whisk|beat|fold|combine|toss|marinate|knead|batter|dough|cream\s+the)\b"),
    ("fridge", r"\b(fridge|refrigerat|chill|chilled|freeze|freezer|overnight|rest\s+in\s+the)\b"),
    ("tray",   r"\b(tray|sheet|line\s+the|arrange|spread\s+out|baking\s+dish)\b"),
    ("plate",  r"\b(serve|serving|plate|garnish|drizzle\s+over|top\s+with|enjoy)\b"),
    ("timer",  r"\b(rest|wait|leave|stand|prove|proof|set\s+aside|cool)\b"),
]

# 180C / 350F / 180 degrees / gas mark 4. All four appear in real recipes and
# all four are the number you want on the oven door.
_TEMP = re.compile(
    r"(\d{2,3})\s*°?\s*(?:c\b|f\b|celsius|fahrenheit|degrees)"
    r"|gas\s*mark\s*(\d)", re.I)
_TIME = re.compile(
    r"(\d+(?:\s*[-–]\s*\d+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b", re.I)

# "1 3/4 cups", "2 tbsp", "1/2 tsp" -- a step's own MID-SENTENCE measurement
# mention, same amount shapes scale.py's own imperial_text() already
# produces (a whole number, a bare fraction, or both together). Volume
# only -- cup/tbsp/tsp are the units someone actually holds a vessel up
# to read, unlike a gram or ounce on a scale, which has nothing to draw.
_AMOUNT = r"(?:\d+\s+\d/\d|\d+/\d+|\d+(?:\.\d+)?)"
_MEASURE = re.compile(
    rf"({_AMOUNT})\s*(cups?|tablespoons?|tbsp\.?|teaspoons?|tsp\.?)\b", re.I)


def _temp_label(text: str) -> str:
    m = _TEMP.search(text)
    if not m:
        return ""
    if m.group(2):
        return f"gas {m.group(2)}"
    unit = "F" if re.search(r"\d\s*°?\s*f\b|fahrenheit", m.group(0), re.I) else "C"
    return f"{m.group(1)}°{unit}"


def _time_label(text: str) -> str:
    m = _TIME.search(text)
    if not m:
        return ""
    unit = m.group(2).lower()
    if unit.startswith(("hour", "hr")):
        short = "hr"
    elif unit.startswith("sec"):
        short = "sec"
    else:
        short = "min"
    amount = re.sub(r"\s+", "", m.group(1))
    return f"{amount} {short}"


def _measure_label(text: str) -> str:
    m = _MEASURE.search(text)
    if not m:
        return ""
    amount = re.sub(r"\s+", " ", m.group(1).strip())
    unit = m.group(2).lower().rstrip(".")
    if unit.startswith("tablespoon") or unit == "tbsp":
        short = "tbsp"
    elif unit.startswith("teaspoon") or unit == "tsp":
        short = "tsp"
    else:
        short = "cup" if amount in ("1", "1.0") else "cups"
    return f"{amount} {short}"


def scene_for(text: str) -> str:
    low = text.lower()
    for name, pattern in _SCENES:
        if re.search(pattern, low):
            return name
    return "bowl"     # a step that is doing something to ingredients, usually


# ---------------------------------------------------------------------------
#  The drawings. 220x150 each, stroke-only, one colour.
# ---------------------------------------------------------------------------
def _oven(temp, _t):
    label = f'<text x="110" y="84" class="big">{html.escape(temp)}</text>' if temp else ""
    return f'''
    <rect x="46" y="26" width="128" height="106" rx="7"/>
    <rect x="58" y="52" width="104" height="66" rx="4"/>
    <line x1="58" y1="64" x2="162" y2="64" class="thin"/>
    <line x1="58" y1="106" x2="162" y2="106" class="thin"/>
    <circle cx="60" cy="38" r="5"/><circle cx="78" cy="38" r="5"/>
    <line x1="96" y1="38" x2="162" y2="38" class="thin"/>
    <line x1="70" y1="118" x2="150" y2="118" stroke-width="4"/>
    {label}'''


def _boil(_temp, t):
    label = f'<text x="110" y="140" class="small">{html.escape(t)}</text>' if t else ""
    return f'''
    <path d="M52 58 L60 122 Q62 132 74 132 L146 132 Q158 132 160 122 L168 58 Z"/>
    <line x1="44" y1="58" x2="176" y2="58" stroke-width="4"/>
    <path d="M62 74 Q110 88 158 74" class="thin"/>
    <circle cx="84" cy="46" r="6" class="thin"/>
    <circle cx="108" cy="34" r="8" class="thin"/>
    <circle cx="132" cy="46" r="5" class="thin"/>
    {label}'''


def _pan(_temp, t):
    label = f'<text x="110" y="142" class="small">{html.escape(t)}</text>' if t else ""
    return f'''
    <path d="M40 68 L46 104 Q48 114 60 114 L136 114 Q148 114 150 104 L156 68 Z"/>
    <line x1="34" y1="68" x2="162" y2="68" stroke-width="4"/>
    <line x1="156" y1="76" x2="204" y2="62" stroke-width="5"/>
    <path d="M62 46 Q70 34 78 46" class="thin"/>
    <path d="M100 42 Q108 28 116 42" class="thin"/>
    <path d="M74 124 Q88 132 102 124 Q116 116 130 124" class="thin"/>
    {label}'''


def _board(_temp, _t):
    return '''
    <rect x="34" y="72" width="118" height="44" rx="6"/>
    <circle cx="146" cy="94" r="5" class="thin"/>
    <path d="M104 34 L176 62 L168 74 L98 50 Z"/>
    <line x1="168" y1="74" x2="196" y2="88" stroke-width="6"/>
    <line x1="54" y1="86" x2="54" y2="102" class="thin"/>
    <line x1="70" y1="86" x2="70" y2="102" class="thin"/>
    <line x1="86" y1="86" x2="86" y2="102" class="thin"/>'''


def _bowl(_temp, _t):
    return '''
    <path d="M40 62 Q40 128 110 128 Q180 128 180 62 Z"/>
    <line x1="32" y1="62" x2="188" y2="62" stroke-width="4"/>
    <path d="M56 78 Q110 96 164 78" class="thin"/>
    <path d="M128 20 Q140 40 134 64" stroke-width="4"/>
    <path d="M120 24 Q132 44 126 66" class="thin"/>'''


def _fridge(_temp, t):
    label = f'<text x="110" y="146" class="small">{html.escape(t)}</text>' if t else ""
    return f'''
    <rect x="66" y="14" width="88" height="120" rx="7"/>
    <line x1="66" y1="56" x2="154" y2="56"/>
    <line x1="142" y1="30" x2="142" y2="46" stroke-width="4"/>
    <line x1="142" y1="70" x2="142" y2="96" stroke-width="4"/>
    <path d="M86 84 l0 20 M78 92 l16 0 M80 86 l12 12 M92 86 l-12 12" class="thin"/>
    {label}'''


def _tray(_temp, _t):
    return '''
    <rect x="30" y="52" width="160" height="76" rx="8"/>
    <rect x="42" y="62" width="136" height="56" rx="5" class="thin"/>
    <circle cx="72" cy="80" r="9"/><circle cx="110" cy="80" r="9"/>
    <circle cx="148" cy="80" r="9"/><circle cx="91" cy="102" r="9"/>
    <circle cx="129" cy="102" r="9"/>'''


def _plate(_temp, _t):
    return '''
    <ellipse cx="110" cy="86" rx="76" ry="40"/>
    <ellipse cx="110" cy="86" rx="54" ry="27" class="thin"/>
    <path d="M92 82 Q110 70 128 82" class="thin"/>
    <line x1="18" y1="60" x2="18" y2="120" stroke-width="4"/>
    <line x1="202" y1="60" x2="202" y2="120" stroke-width="4"/>
    <line x1="196" y1="60" x2="196" y2="82" class="thin"/>
    <line x1="208" y1="60" x2="208" y2="82" class="thin"/>'''


def _timer(_temp, t):
    label = f'<text x="110" y="93" class="big">{html.escape(t)}</text>' if t else ""
    hand = "" if t else '<line x1="110" y1="82" x2="110" y2="48" stroke-width="4"/>'
    return f'''
    <circle cx="110" cy="82" r="52"/>
    <line x1="110" y1="30" x2="110" y2="24" stroke-width="4"/>
    <line x1="162" y1="82" x2="168" y2="82" stroke-width="4"/>
    <line x1="110" y1="134" x2="110" y2="140" stroke-width="4"/>
    <line x1="58" y1="82" x2="52" y2="82" stroke-width="4"/>
    <path d="M96 22 L124 22" stroke-width="5"/>
    {hand}{label}'''


def _measure(label):
    text = f'<text x="110" y="120" class="big">{html.escape(label)}</text>' if label else ""
    return f'''
    <path d="M64 40 L156 40 L146 128 Q144 138 132 138 L88 138 Q76 138 74 128 Z"/>
    <path d="M156 42 L184 34" class="thin"/>
    <line x1="66" y1="62" x2="154" y2="62" class="thin"/>
    <line x1="70" y1="88" x2="150" y2="88" class="thin"/>
    <path d="M60 52 Q42 58 42 72 Q42 86 60 92" class="thin"/>
    {text}'''


_DRAW = {"oven": _oven, "boil": _boil, "pan": _pan, "board": _board,
         "bowl": _bowl, "fridge": _fridge, "tray": _tray, "plate": _plate,
         "timer": _timer}


def svg_for(step_text: str, number: int) -> str:
    """One diagram for one step. Always returns something drawable."""
    scene = scene_for(step_text)
    temp = _temp_label(step_text)
    time = _time_label(step_text)
    # The oven drawing shows a temperature; if the step has none, a time is
    # the next most useful number to put in front of someone.
    body = _DRAW[scene](temp or "", time or "")

    return (
        f'<svg class="dgm" viewBox="0 0 220 150" role="img" '
        f'aria-label="Diagram for step {number}: {html.escape(scene)}">'
        '<g fill="none" stroke="currentColor" stroke-width="2.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'{body}'
        '</g>'
        f'<circle cx="24" cy="24" r="15" fill="currentColor"/>'
        f'<text x="24" y="24" class="num">{number}</text>'
        '</svg>'
    )


def _badge_svg(kind: str, label: str, body: str) -> str:
    return (
        f'<svg class="dgm badge" viewBox="0 0 220 150" role="img" '
        f'aria-label="{html.escape(kind)}: {html.escape(label)}">'
        '<g fill="none" stroke="currentColor" stroke-width="2.5" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'{body}'
        '</g></svg>'
    )


def badges_for(step_text: str) -> list:
    """Small tappable call-outs for a number worth SEEING, not just
    reading -- Jason's own request (2026-08-25): "when a timer needs to
    be set... setting a temp to 400 degrees... illustrations of
    measurements in the steps... just popup the image of this." He
    proposed pre-rendering these with a model; they are drawn by code
    instead, same reasoning as svg_for() above and the module's own
    docstring -- a vector oven dial or timer face is instant, free, and
    can never show the wrong number the way an AI image or a stale
    pre-rendered library entry could.

    Different from svg_for() on purpose: that function picks ONE scene
    for the whole step (there is only one diagram slot where a photo
    would otherwise go). A step can mention more than one of these at
    once ("heat to 400°F for 15 minutes"), so this returns a LIST, one
    entry per KIND that appears (temp, time, measure), first mention
    only within a kind -- a step repeating "350°F" twice does not need
    the oven drawn twice. Each entry is {"kind", "label", "svg"}; an
    empty list means nothing in this step is worth a call-out."""
    out = []
    temp = _temp_label(step_text)
    if temp:
        out.append({"kind": "temp", "label": temp,
                    "svg": _badge_svg("temp", temp, _oven(temp, ""))})
    time = _time_label(step_text)
    if time:
        out.append({"kind": "time", "label": time,
                    "svg": _badge_svg("time", time, _timer("", time))})
    measure = _measure_label(step_text)
    if measure:
        out.append({"kind": "measure", "label": measure,
                    "svg": _badge_svg("measure", measure, _measure(measure))})
    return out
