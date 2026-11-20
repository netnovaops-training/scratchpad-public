"""Signing somebody into Tandoor without asking them for a password.

Same problem and same shape as handoff.py's Mealie hand-off -- read that
file's own docstring for the full reasoning, this one only writes down
what is actually DIFFERENT about Tandoor's side of it.

WHAT'S DIFFERENT FROM MEALIE
-----------------------------
Mealie hands out a bearer token from a JSON login endpoint. Tandoor's REST
API only accepts a Django session cookie or a real OAuth2 access token --
CHECKED LIVE: DEFAULT_AUTHENTICATION_CLASSES is SessionAuthentication and
OAuth2Authentication only, no plain-token auth wired to any view despite
`manage.py drf_create_token` existing (that token is created but nothing
ever checks it). The client-credentials OAuth2 grant -- the intended
machine-to-machine path -- 403s on a CSRF error that was not root-caused
in a first pass. What IS confirmed working, live: log in through
Tandoor's own form (GET the login page for a CSRF token, POST the
credentials, keep the `sessionid` cookie that comes back) and use that
cookie for every API call afterward. So this hands the browser that SAME
session cookie, not a bearer token.

WHY PER-PERSON ACCOUNTS ARE HAND-PROVISIONED, NOT MADE ON FIRST SIGHT.
Mealie's hand-off creates a Mealie account per household member the
first time it ever sees their email (handoff.py's ensure_user()), because
Mealie's own sign-up is reachable and safe to drive from code. CHECKED
LIVE on Tandoor: /accounts/signup/ renders "Sign Up Closed" -- public
registration is off, on purpose, and turning it on just to script around
it would open a real sign-up form to the internet. So the household
(Jason, Mama, Casey -- people.py's OWNERS | WRITERS) was created once,
by hand, straight into Tandoor's database: same Space ("Jasons Space"),
same Household ("Home"), group "admin" for Jason and "user" for
everyone else. Their generated secrets live in Kitchen's own
tandoor_users table (store.tandoor_secret/set_tandoor_secret), same shape
as mealie_secret. A new household member added to people.json later
needs the same one-time step repeated by hand -- this module does not
attempt it, and ensure_user() below says so plainly rather than failing
silently.

WHY THE COOKIE REACHES TANDOOR AT ALL
--------------------------------------
Same reasoning as Mealie's: this app is served from
example.org/kitchen and Tandoor from
example.org:9445 -- different ports, same host. A cookie set
here with no Domain attribute is a host-only cookie, sent to every port
on this hostname, which is the whole mechanism.
"""
import re

import httpx

from . import core, people, store

COOKIE = "sessionid"
_CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


class HandoffError(Exception):
    """Could not hand this person over. The message is shown to them."""


def ensure_user(email: str) -> str:
    """The Tandoor username to sign this person in as.

    Owners sign in as the real admin account (it IS Jason's account, made
    through Tandoor's own setup wizard, not a stand-in for it). Everyone
    else on the sign-in list uses their own hand-provisioned account --
    see the module docstring for why those are made ahead of time instead
    of here. Raises rather than falling back to the admin account for a
    household member with no row yet, so a missed provisioning step is a
    clear error instead of everyone quietly sharing Jason's account.
    """
    if not people.is_allowed(email):
        raise HandoffError("You are not on the Kitchen sign-in list.")
    if people.is_owner(email):
        return core.TANDOOR_ADMIN_USER
    if store.tandoor_secret(email) is None:
        raise HandoffError(
            f"{email} does not have a Tandoor account yet -- this has to be "
            "made once by hand (Tandoor's own sign-up is closed on purpose). "
            "Tell Jason.")
    return email


async def _sign_in(username: str, password: str) -> str | None:
    """A real Tandoor session id, or None if that password is wrong.

    TWO REQUESTS, ONE CLIENT, so the CSRF cookie the first request sets is
    still attached for the second -- Django's CSRF check needs both the
    cookie and the matching form field, not just the field alone.
    """
    async with httpx.AsyncClient(base_url=core.TANDOOR_URL, timeout=20) as client:
        r = await client.get("/accounts/login/")
        m = _CSRF_RE.search(r.text)
        if not m:
            raise HandoffError(
                "Tandoor's sign-in page did not have the form I expected -- "
                "it may have changed, or the setup wizard was never finished.")
        r2 = await client.post(
            "/accounts/login/",
            data={
                "csrfmiddlewaretoken": m.group(1),
                "login": username,
                "password": password,
            },
            headers={"Referer": f"{core.TANDOOR_URL}/accounts/login/"},
        )
        return r2.cookies.get(COOKIE)


async def mint(email: str) -> str:
    """A real Tandoor session id for whichever account this person maps to."""
    username = ensure_user(email)
    password = (core.TANDOOR_ADMIN_PASSWORD if people.is_owner(email)
                else store.tandoor_secret(email))
    if not username or not password:
        raise HandoffError(
            "TANDOOR_ADMIN_USER / TANDOOR_ADMIN_PASSWORD are not set in .env, "
            "so I have nothing to sign the admin account in with.")
    try:
        session = await _sign_in(username, password)
    except httpx.RequestError as e:
        raise HandoffError(f"Could not reach Tandoor ({e.__class__.__name__}). "
                          "Is the container running?")
    if not session:
        raise HandoffError(
            f"Tandoor would not sign {username} in -- the stored password has "
            "gone stale. It needs to be reset by hand the same way it was "
            "first made, then updated here to match.")
    return session


def cookie(session_id: str) -> str:
    """The Set-Cookie value that signs the browser into Tandoor.

    Path=/ and NO Domain attribute, same reasoning as Mealie's own
    handoff.cookie(). Max-Age matches Django's own SESSION_COOKIE_AGE
    (checked live: 31536000 seconds, Tandoor's own one-year default) --
    not longer, so a cookie that outlives Tandoor's own session record
    never happens.
    """
    parts = [
        f"{COOKIE}={session_id}",
        "Path=/",
        "SameSite=Lax",
        f"Max-Age={core.TANDOOR_SESSION_SECONDS}",
    ]
    if core.COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)
