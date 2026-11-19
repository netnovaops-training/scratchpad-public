"""
Keeping health information out of the record.

THE CORE IDEA: ALLOWLIST, NOT DENYLIST
======================================
The obvious approach is to write down the call and then strip out the medical
bits. That approach always leaks, and it leaks in the worst way -- quietly.
No pattern list catches:

    "she's been a lot slower since the stroke"
    "he doesn't have very long now"
    "the doctor said maybe a few weeks"
    "she gets confused in the afternoons"

Those are ordinary sentences. They are also, between them, a diagnosis, a
prognosis, a terminal condition and a dementia symptom. A denylist that catches
"stroke" misses the other three, and you would never know.

So this module inverts it. A call produces a record with a FIXED set of
fields -- who, how to reach them, where they're going, how long, when. That is
everything Dee needs to do the visit. Anything a caller says that doesn't fit
one of those fields is not redacted; it is simply never written anywhere. There
is no bucket for it.

The pattern scrubbing below is a SECOND line of defence for the free-text
fields that do survive, not the primary one. Defence in depth, with the
structure doing the real work.

WHY THIS MATTERS HERE
=====================
This business is not HIPAA-covered -- no medical records, no insurance billing,
no provider relationship. HIPAA is the wrong statute.

The one that applies is Nevada's consumer health data law, NRS 603A.400-550,
and it is stricter in the way that matters: it has no small-business exemption,
and its only relevant carve-out is for HIPAA-covered entities. Not being
HIPAA-covered removes the exemption rather than granting one.

It covers information "used to identify" someone's health status -- explicitly
including conditions, diagnoses, medications, interventions, and information
"derived or extrapolated from non-health data" by machine learning. Families of
hospice clients volunteer exactly this on the phone, unprompted, constantly.

Collecting it requires affirmative consent. Not collecting it requires nothing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# =============================================================================
#  THE ALLOWLIST -- the only things a call is permitted to leave behind.
# =============================================================================
#
# Adding a field here is a decision to start collecting something. Do it
# deliberately. There is intentionally no "notes" or "other" field: the moment
# one exists, everything ends up in it and the allowlist stops meaning anything.

@dataclass
class CallRecord:
    """What one call is allowed to become."""

    # -- who, and how to reach them ------------------------------------------
    caller_phone: str = ""          # the number that rang
    caller_name: str = ""           # what they said their name was
    client_code: str = ""           # 5-digit code for the person being visited
    client_name: str = ""           # who the visit is FOR
    callback_phone: str = ""        # if different from caller_phone
    relationship: str = ""          # "daughter", "neighbour" -- who they are to the client

    # -- the logistics of the visit ------------------------------------------
    pickup_address: str = ""
    destination: str = ""           # generalised; see generalise_destination()
    when: str = ""                  # ISO datetime or "Tuesday afternoon"
    duration_minutes: int = 0
    service: str = ""               # visit_short | visit_half_day | ...

    # -- practical needs, NOT clinical ---------------------------------------
    # The distinction is whether Dee needs it to do the job, or whether it
    # describes the person's health. "Uses a walker" is the former: it decides
    # whether the car works. "Has Parkinson's" is the latter, and Dee does not
    # need it to drive someone to the shops.
    mobility_aid: str = ""          # walker | wheelchair | cane | none
    needs_help_to_car: bool = False

    # -- what the call was about ---------------------------------------------
    outcome: str = ""               # booked | message | callback | cancelled | enquiry
    booking_reference: str = ""

    # -- provenance -----------------------------------------------------------
    call_id: str = ""
    started_at: str = ""
    seconds: int = 0
    transcript_kept: bool = False   # did they consent?

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Every free-text field gets scrubbed before it is stored. Listed explicitly so
# adding a field forces a decision about whether it needs scrubbing.
FREE_TEXT_FIELDS = (
    "caller_name", "client_name", "relationship",
    "pickup_address", "destination", "when", "service", "mobility_aid",
)


# =============================================================================
#  Second line of defence: pattern scrubbing.
# =============================================================================

# Conditions, diagnoses, prognoses. Deliberately broad -- a false positive
# costs a word in a field Dee can ask about; a false negative costs a record
# of someone's diagnosis.
_CONDITIONS = r"""
alzheimer\w*|dementia|parkinson\w*|cancer|carcinoma|tumou?r|leukemia|lymphoma|
stroke|aneurysm|seizure\w*|epilep\w+|diabet\w+|copd|emphysema|asthma|
kidney\s+(?:disease|failure)|renal|liver\s+(?:disease|failure)|cirrhosis|
heart\s+(?:attack|failure|disease)|cardiac|arrhythmia|angina|
hospice|palliative|terminal\w*|end[-\s]stage|
incontinen\w+|bedsore\w*|pressure\s+sore\w*|ulcer\w*|
depress\w+|anxiet\w+|bipolar|schizophren\w+|psychosis|
osteoporosis|arthrit\w+|fibromyalgia|neuropath\w+|
# "broke her hip" is how people actually say it -- "broken hip" alone misses it.
brok\w*\s+(?:her|his|their|a|the)?\s*(?:hip|leg|arm|wrist|back)|
broken\s+hip|fractur\w+|amputat\w+|
bedridden|bed[-\s]bound|wheelchair[-\s]bound|
can'?t\s+(?:walk|swallow|feed\s+herself|feed\s+himself)
"""

# Medications: named drugs plus the suffixes that mark a drug class.
_MEDICATIONS = r"""
morphine|oxycodone|oxycontin|fentanyl|hydrocodone|codeine|tramadol|
insulin|metformin|warfarin|coumadin|eliquis|xarelto|
lasix|furosemide|prednisone|gabapentin|lisinopril|amlodipine|
donepezil|aricept|memantine|namenda|levodopa|sinemet|
ativan|lorazepam|xanax|valium|haldol|seroquel|
chemo\w*|chemotherapy|
\w+(?:pril|olol|statin|azepam|codone|cillin|prazole|sartan)\b
"""

# Treatments, procedures, and the clinicians who deliver them.
_TREATMENTS = r"""
dialysis|chemotherapy|radiation|immunotherapy|transfusion|infusion|
surgery|surgical|operation|biopsy|catheter\w*|feeding\s+tube|
ventilator|oxygen(?:\s+(?:tank|therapy|concentrator))?|nebuli\w+|
physical\s+therapy|occupational\s+therapy|
oncolog\w+|cardiolog\w+|neurolog\w+|nephrolog\w+|psychiatr\w+|
dnr|do\s+not\s+resuscitate|advance\s+directive|living\s+will
"""

_HEALTH = re.compile(
    "|".join(p.strip() for p in (_CONDITIONS, _MEDICATIONS, _TREATMENTS)),
    re.IGNORECASE | re.VERBOSE,
)

REDACTED = "[health info removed]"


def scrub(text: str) -> str:
    """
    Replace health terms with a marker.

    The marker is left visible rather than deleted silently, so that anyone
    reading a record can see something was removed and isn't misled into
    thinking the caller never mentioned it. Transparency about the redaction,
    without the content.
    """
    if not text:
        return text
    out = _HEALTH.sub(REDACTED, text)

    # Collapse runs, so "chemo and dialysis" is one marker rather than two.
    #
    # NOTE the explicit alternation. The obvious version of this uses a
    # character class -- [\s,and]* -- which is a trap: a character class of
    # "a", "n", "d" also eats the leading letters of the NEXT word. It turned
    # "bedridden now" into "...ow" and "can't walk anymore" into "...ymore",
    # silently corrupting text just past the redaction.
    marker = re.escape(REDACTED)
    out = re.sub(rf"{marker}(?:\s*(?:,|and|&)?\s*{marker})+", REDACTED, out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def mentions_health(text: str) -> bool:
    """Did they bring up health? Used to decide whether to keep a field at all."""
    return bool(text) and bool(_HEALTH.search(text))


# =============================================================================
#  Destinations -- the genuinely hard case.
# =============================================================================
#
# "Take her to the dialysis centre on Rancho" is BOTH an operational fact Dee
# needs and a diagnosis. Dropping it entirely means she doesn't know where to
# drive; keeping it verbatim records a kidney condition.
#
# So: keep the address, generalise the specialty. She gets "medical appointment
# on Rancho", drives there, and sees for herself. Nothing is inferable from the
# record that isn't already obvious to anyone standing in the car park.

_MEDICAL_PLACES = re.compile(
    r"\b("
    r"dialysis(?:\s+cent(?:er|re))?|oncology|chemo\w*|infusion\s+cent(?:er|re)|"
    r"cardiology|neurology|psychiatr\w+|"
    r"hospice(?:\s+(?:house|cent(?:er|re)))?|"
    r"cancer\s+cent(?:er|re)|"
    r"(?:doctor|dr\.?|physician|specialist)(?:'?s)?(?:\s+office)?|"
    r"clinic|hospital|medical\s+cent(?:er|re)|urgent\s+care|"
    r"pharmacy|drug\s?store|chemist"
    r")\b",
    re.IGNORECASE,
)

# Kept as-is: these are errands, not health.
_ERRANDS = re.compile(
    r"\b(grocer\w+|market|walmart|target|costco|bank|church|library|park|"
    r"salon|barber|post\s+office|dmv|restaurant|lunch|dinner|coffee|mall)\b",
    re.IGNORECASE,
)


def generalise_destination(text: str) -> str:
    """
    Keep where. Drop what for.

    "the dialysis centre at 4500 Rancho"  ->  "medical appointment at 4500 Rancho"
    "Smith's on Craig Road"               ->  unchanged
    """
    if not text:
        return text
    if _ERRANDS.search(text) and not _MEDICAL_PLACES.search(text):
        return text

    if _MEDICAL_PLACES.search(text):
        # Pharmacy is worth keeping distinct: it's a routine errand and Dee
        # collecting a prescription is the actual service, not a diagnosis.
        if re.search(r"\b(pharmacy|drug\s?store|chemist)\b", text, re.IGNORECASE):
            out = _MEDICAL_PLACES.sub("pharmacy", text)
        else:
            out = _MEDICAL_PLACES.sub("medical appointment", text)
        # A specialty can survive elsewhere in the string -- "oncology" caught,
        # but "for her cancer" not. Scrub the remainder.
        return scrub(out)

    return scrub(text)


# =============================================================================
#  Building the record
# =============================================================================

def clean_record(rec: CallRecord) -> CallRecord:
    """
    Last gate before anything is written. Everything goes through here.

    Applies to the record that ALREADY only has allowlisted fields -- so this
    is tidying, not the main protection.
    """
    rec.destination = generalise_destination(rec.destination)

    for name in FREE_TEXT_FIELDS:
        if name == "destination":
            continue
        val = getattr(rec, name, "")
        if isinstance(val, str) and val:
            setattr(rec, name, scrub(val))

    # A mobility aid is practical. Anything else in that field is clinical and
    # gets dropped rather than marked, because there's no useful remainder.
    if rec.mobility_aid and not re.fullmatch(
        r"\s*(walker|wheelchair|cane|walking\s+stick|rollator|scooter|none|)\s*",
        rec.mobility_aid, re.IGNORECASE,
    ):
        rec.mobility_aid = ""

    return rec


def from_extraction(data: dict[str, Any]) -> CallRecord:
    """
    Turn whatever the model produced into a CallRecord.

    Unknown keys are DISCARDED, silently and on purpose. If the model decides
    to return {"medical_history": "..."} because a caller volunteered it, that
    key does not exist on CallRecord and there is nowhere for the value to go.
    That is the allowlist doing its job -- and it's why this takes a dict and
    picks fields out, rather than doing CallRecord(**data).
    """
    allowed = {f.name for f in CallRecord.__dataclass_fields__.values()}
    rec = CallRecord()
    for key, value in (data or {}).items():
        if key not in allowed or value in (None, ""):
            continue
        current = getattr(rec, key)
        try:
            if isinstance(current, bool):
                setattr(rec, key, bool(value))
            elif isinstance(current, int):
                setattr(rec, key, int(value))
            else:
                setattr(rec, key, str(value))
        except (TypeError, ValueError):
            continue
    return clean_record(rec)


# =============================================================================
#  What the assistant is told
# =============================================================================
#
# The cheapest control by a distance. Not eliciting health information is
# better than collecting and scrubbing it, and it's a paragraph.

PROMPT_RULES = """
ABOUT HEALTH INFORMATION -- this matters:

Never ask about health. Not medications, diagnoses, treatments, prognosis,
symptoms, mental state, or how someone is doing medically. It is not needed to
arrange a visit and this service does not collect it.

Callers will volunteer it anyway -- especially families of people who are very
unwell. When they do:
  - Respond warmly. Do not correct them, and do not say anything about privacy
    policies. Someone telling you their mother is dying needs kindness, not a
    disclaimer.
  - Do NOT repeat it back, and do NOT write it into any field.
  - Move gently to what you do need: where to be, when, and for how long.

You may record only: who is calling, who the visit is for, how to reach them,
where to collect them, where they're going, when, how long, and whether they
need help getting to the car.

If someone is going to a medical appointment, record it as "medical
appointment" and the address. Never the specialty or the reason.

You are not a nurse, a counsellor or a doctor. Do not give medical advice, do
not discuss medication, and do not offer therapy or grief counselling. If asked
for any of those, say plainly that Dee will call them back personally.
"""
