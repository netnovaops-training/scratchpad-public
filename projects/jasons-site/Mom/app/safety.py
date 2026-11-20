"""Safety tripwires.

An AI that patiently walks a non-technical person through computer steps is
exactly the tool a scammer would want pointed at their victim. The realistic bad
outcome is not the model being rude -- it is the model helpfully explaining how
to install remote-access software, or how to find the Google Play gift card
aisle, because she asked it to in good faith while a "Microsoft technician" was
on the phone telling her what to say.

So the rules here are enforced in CODE, not in the prompt. A system prompt can
be talked around by a confused user relaying a scammer's script. A hardcoded
pattern match cannot.

Two layers:
  1. screen_user()      -- what she typed or said. CRITICAL hits never reach the
                           model at all; a fixed response is returned instead.
  2. screen_assistant() -- what the model produced. If it is about to give
                           dangerous instructions, the reply is replaced.

Both raise an alert so the family member sees it on the admin page.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

CRITICAL = "critical"
WARN = "warn"


@dataclass
class Screen:
    level: Optional[str] = None
    flags: list[str] = field(default_factory=list)
    reason: str = ""
    replacement: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.level == CRITICAL


def _rx(*words: str) -> re.Pattern:
    return re.compile("|".join(words), re.I)


# --- what she might be being talked into ------------------------------------

REMOTE_ACCESS = _rx(
    r"\banydesk\b", r"\bteamviewer\b", r"\bteam viewer\b", r"\blogmein\b",
    r"\bultraviewer\b", r"\bsupremo\b", r"\bconnectwise\b", r"\bscreenconnect\b",
    r"\bgotoassist\b", r"\bsplashtop\b", r"\brustdesk\b", r"\bquick assist\b",
    r"\bremote (?:desktop|access|support|connection)\b",
    r"\b(?:let|allow|give|grant)\s+(?:him|her|them|someone|the (?:tech|guy|man|agent))\s+"
    r"(?:in|access|control|remote)",
    r"\btake (?:over|control of) my (?:computer|screen|pc|laptop)\b",
    r"\bsee my screen\b", r"\bcontrol my (?:computer|pc|laptop|screen)\b",
)

PAYMENT = _rx(
    r"\bgift card\b", r"\bgiftcard\b", r"\bapple card\b", r"\bgoogle play card\b",
    r"\bsteam card\b", r"\bitunes card\b", r"\bvanilla card\b", r"\bprepaid card\b",
    r"\bwire (?:transfer|money|the money)\b", r"\bzelle\b", r"\bvenmo\b",
    r"\bcash app\b", r"\bmoneygram\b", r"\bwestern union\b",
    r"\bbitcoin\b", r"\bcrypto\b", r"\bcoinbase\b", r"\bbitcoin atm\b",
    r"\b(?:send|wire|transfer|pay|deposit)\s+(?:them|him|her|it)?\s*\$?\d",
    r"\bbuy (?:a |some )?(?:gift )?cards?\b",
    r"\bscratch off the back\b", r"\bread (?:me )?the (?:numbers|code) on the (?:back|card)\b",
)

CREDENTIALS = _rx(
    r"\b(?:verification|security|one[- ]time|authentication|confirmation) code\b",
    r"\b2fa\b", r"\bcode (?:they|he|she|it) (?:sent|texted)\b",
    r"\bread (?:him|her|them|it) (?:the|my) (?:code|password|pin)\b",
    r"\bgive (?:him|her|them) my password\b",
    r"\bsocial security (?:number|#)\b", r"\brouting number\b",
    r"\bbank (?:account|login|password)\b",
    r"\bmy password is\b", r"\bmy pin is\b",
)

SCAM_STORY = _rx(
    r"\bmicrosoft (?:called|support called|technician)\b",
    r"\bapple (?:called|support called)\b",
    r"\b(?:someone|a man|a guy|a lady|they) (?:called|phoned) (?:me )?(?:and )?said\b",
    r"\bpop[- ]?up (?:says|said|saying)\b.*\b(?:call|virus|infected|locked|banned)\b",
    r"\byour computer (?:is|has been) (?:infected|locked|compromised|hacked)\b",
    r"\bcall this number\b", r"\bdo not (?:turn off|shut down|restart) your computer\b",
    r"\bsubscription (?:has )?(?:renewed|expired)\b.*\b(?:refund|cancel|charge)\b",
    r"\brefund (?:department|too much|overpaid)\b",
    r"\bgrandson\b.*\b(?:jail|bail|arrested|trouble|money)\b",
    r"\bgrandma,? (?:it's me|i'm in trouble)\b",
    r"\bwarrant (?:for your arrest|out for)\b",
    r"\bIRS\b.*\b(?:owe|arrest|warrant|pay)\b",
    r"\bstay on the (?:phone|line) (?:with me|while)\b",
    # The secrecy tell, in every phrasing people actually use. This one matters
    # more than any other single pattern: there is no innocent reason for a
    # stranger to ask someone not to tell their family.
    # The object is REQUIRED here. An earlier, looser version matched
    # "can I not tell the app my location", which is a perfectly ordinary
    # privacy question.
    r"\b(?:don'?t|do not|shouldn'?t|not to)\s+tell\s+"
    r"(?:anyone|anybody|(?:my|your|the)\s+"
    r"(?:family|son|daughter|kids|children|husband|wife|bank|sister|brother))\b",
    r"\b(?:said|told me|asked me)\s+(?:me\s+)?not to tell\b",
    r"\bkeep (?:this|it) (?:a )?secret\b",
    r"\bbetween (?:you and me|us)\b.*\b(?:money|card|payment|account)\b",
)

SECURITY_OFF = _rx(
    r"\b(?:turn off|disable|uninstall|remove|stop)\s+(?:my\s+|the\s+|windows\s+)?"
    r"(?:defender|antivirus|anti-virus|firewall|virus protection|security)\b",
    r"\b(?:allow|whitelist|add an exception for)\b.*\bdefender\b",
)

DESTRUCTIVE = _rx(
    r"\bregedit\b", r"\bregistry editor\b", r"\bdelete system32\b",
    r"\bformat (?:c:|the (?:hard )?drive)\b", r"\bfactory reset\b",
    r"\bdiskpart\b", r"\bbcdedit\b", r"\bsafe mode with networking\b",
    r"\bcommand prompt\b", r"\bpowershell\b", r"\bcmd\.exe\b",
    r"\bwin\+r\b", r"\brun (?:dialog|box)\b", r"\beventvwr\b",
)

# Only meaningful in the ASSISTANT's output -- these are things it must never
# instruct her to do, regardless of how the request was phrased.
ASSISTANT_FORBIDDEN = [
    ("remote_access", REMOTE_ACCESS,
     "told her how to give someone remote access"),
    ("payment", PAYMENT,
     "gave instructions involving gift cards, transfers, or cryptocurrency"),
    ("security_off", SECURITY_OFF,
     "told her to disable antivirus or the firewall"),
    ("destructive", DESTRUCTIVE,
     "told her to use the registry, command prompt, or a destructive tool"),
]


SCAM_RESPONSE = """\
## Stop — this looks like a scam

I want you to stop what you're doing for a second. What you've described matches
a scam that targets people every single day. You have not done anything wrong,
and nothing bad has happened yet.

**Please do these three things now:**

1. **Hang up the phone**, or close the pop-up window. It is safe to do that.
   Nothing will break. Real companies never call you like this.
2. **Do not** give anyone access to your computer, and **do not** buy any gift
   cards, send money, or read any codes out loud.
3. **Call {helper}.** Don't do anything else until you've spoken to him.

Some things worth knowing, so this is easier to spot next time:

- Microsoft, Apple, Amazon, and your bank **will never call you** about a virus
  or a refund. Not ever.
- A pop-up that says your computer is infected and gives you a phone number
  **is always fake**. The real message is: close the browser.
- Anyone who asks for **gift cards** is a scammer. There are no exceptions to
  this. No real company, agency, or police force is paid in gift cards.
- Anyone who tells you **not to tell your family** is a scammer. That request
  exists for exactly one reason.

I'm not able to help with this particular thing — that's on purpose, because
helping would be the wrong thing to do. But once you've talked to {helper}, come
back and ask me anything else and I'll be glad to help.
"""

BLOCKED_RESPONSE = """\
## I can't help with that one

I'm not able to walk you through this, and I want to be straight with you about
why rather than pretend I don't understand.

{why}

This is one of the few things I'm set up to refuse, because if someone had asked
you to do it, helping would put you at real risk. If it's a legitimate need,
{helper} can do it with you in a couple of minutes.

**Call {helper}.** Ask me anything else in the meantime.
"""

WHY = {
    "remote_access": "You've asked about letting someone else control your "
                     "computer from far away. That is the single most common way "
                     "people lose money to scams, so I won't set it up.",
    "payment": "You've asked about gift cards, transfers, or cryptocurrency. "
               "Legitimate companies never ask to be paid that way, so I won't "
               "give instructions for it.",
    "credentials": "You've asked about codes, passwords, or account numbers. "
                   "Nobody legitimate ever needs you to read those to them.",
    "security_off": "You've asked about turning off your antivirus or firewall. "
                    "Those are what keep you safe, and scammers ask for this "
                    "first.",
}


def screen_user(text: str, helper: str = "Jason") -> Screen:
    """Look at what she said BEFORE it reaches the model."""
    t = text or ""
    flags: list[str] = []

    scam = bool(SCAM_STORY.search(t))
    remote = bool(REMOTE_ACCESS.search(t))
    pay = bool(PAYMENT.search(t))
    cred = bool(CREDENTIALS.search(t))
    secoff = bool(SECURITY_OFF.search(t))

    for name, hit in (("scam_story", scam), ("remote_access", remote),
                      ("payment", pay), ("credentials", cred),
                      ("security_off", secoff)):
        if hit:
            flags.append(name)

    # A scam narrative combined with ANY of the payloads is an in-progress
    # attack. So is a payment or credentials request on its own -- there is no
    # innocent version of "how do I buy a gift card for the Microsoft man".
    if scam and (remote or pay or cred or secoff):
        return Screen(CRITICAL, flags, "scam narrative plus payload",
                      SCAM_RESPONSE.format(helper=helper))
    if pay or cred:
        return Screen(CRITICAL, flags, "payment or credential request",
                      SCAM_RESPONSE.format(helper=helper))
    if remote or secoff:
        key = "remote_access" if remote else "security_off"
        return Screen(CRITICAL, flags, key,
                      BLOCKED_RESPONSE.format(why=WHY[key], helper=helper))
    if scam:
        # A scam story with no payload yet: still stop and warn. Better a false
        # positive on a story about a weird phone call than a miss.
        return Screen(CRITICAL, flags, "scam narrative",
                      SCAM_RESPONSE.format(helper=helper))

    if DESTRUCTIVE.search(t):
        flags.append("destructive")
        return Screen(WARN, flags, "asked about an advanced or destructive tool")
    return Screen(None, flags)


def screen_assistant(text: str, helper: str = "Jason") -> Screen:
    """Look at what the model produced BEFORE she sees it."""
    t = text or ""
    for name, pattern, why in ASSISTANT_FORBIDDEN:
        if pattern.search(t):
            return Screen(
                CRITICAL, [f"assistant_{name}"], why,
                BLOCKED_RESPONSE.format(
                    why=WHY.get(name, "The steps involved are risky enough that I "
                                      "shouldn't give them to you on my own."),
                    helper=helper))
    return Screen(None, [])


# --- keep obvious secrets out of the database -------------------------------

_REDACT = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[removed]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[card number removed]"),
    (re.compile(r"\b\d{6,10}\b(?=\s*(?:is|was)?\s*(?:the )?(?:code|pin))", re.I), "[code removed]"),
]


def redact(text: str) -> tuple[str, bool]:
    hit = False
    for pattern, rep in _REDACT:
        text, n = pattern.subn(rep, text)
        hit = hit or bool(n)
    return text, hit
