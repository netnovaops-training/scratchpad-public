"""Signing into OTOBO without retyping its password every time.

WHY THIS IS NOT LIKE KITCHEN'S MEALIE HAND-OFF
-------------------------------------------------
Mealie's hand-off (Kitchen/app/handoff.py) creates an account for whoever
signs in, on first sight, because Mealie has a real admin REST API for user
management. OTOBO does not -- checked directly, there is no
GenericInterface Operation anywhere under Kernel/GenericInterface/Operation
for creating or managing agents. This cannot do what Mealie's hand-off
does, and it does not need to: OTOBO was never meant for anyone but you
(see the note at the top of otobo.py -- Mama never touches :9444). One
fixed agent, `jason-sso`, made once by hand (see OTOBO.md), is the entire
population this ever signs in.

WHY THIS GOES THROUGH THE REAL PUBLIC HTTPS ADDRESS, NOT otobo:5000 DIRECTLY
-------------------------------------------------------------------------------
CAUGHT LIVE: OTOBO only marks its session cookie Secure when its own
`HttpType` setting is 'https' (confirmed directly in
Kernel/Output/HTML/Layout.pm: `$Secure //= $ConfigObject->Get('HttpType')
eq 'https'`), and a Secure cookie is refused by any well-behaved client --
including this one -- over a plain HTTP connection. otobo.py's own calls go
straight to http://otobo:5000 because they use a bearer-token REST session
that never touches a cookie at all. This one has to look exactly like a
real browser, so it calls https://example.org:9444 through the
SAME Caddy, the same real certificate, every browser uses -- see the
`example.org` network alias on the caddy service in
docker-compose.web.yml, which is what lets a container reach that hostname
(and get the right TLS SNI, which is set from the hostname connected to,
not any header -- see web/Caddyfile's own :9444 note for that lesson
learned the hard way) without going out to the real internet and back.

And because that address sits behind forward_auth same as everywhere else,
this forwards YOUR OWN site session cookie on every request -- the exact
cookie your browser already presented to reach the page that called this.
That is the whole second half of the trick: forward_auth lets it through
for the same reason it let your browser through a moment ago.
"""
import os

import httpx

OTOBO_PUBLIC = "https://example.org:9444"
LOGIN_PATH = "/otobo/index.pl"
SITE_COOKIE = "sso"
PROBE_COOKIE = "OTOBOBrowserHasCookie"

AGENT_LOGIN = os.getenv("MOM_OTOBO_SSO_LOGIN", "jason-sso")
AGENT_PASSWORD = os.getenv("MOM_OTOBO_SSO_PASSWORD", "")


class HandoffError(Exception):
    """Could not sign in. The message is shown to the person, not a 500."""


def mint(site_cookie: str) -> str:
    """The full Set-Cookie header value OTOBO issued on a real login, ready
    to hand straight back to the browser. Raises HandoffError with a
    message worth showing directly rather than a stack trace.
    """
    if not AGENT_PASSWORD:
        raise HandoffError(
            "MOM_OTOBO_SSO_PASSWORD is not set -- see OTOBO.md for the "
            "one-time step that creates the jason-sso agent and its password.")
    if not site_cookie:
        raise HandoffError("No site session to hand off -- sign in again.")

    try:
        with httpx.Client(timeout=20, cookies={SITE_COOKIE: site_cookie}) as client:
            # Step 1: the probe. OTOBO's login refuses to work at all until
            # it has seen this cookie come back on a second request -- its
            # own defence against a browser with cookies disabled.
            probe = client.get(f"{OTOBO_PUBLIC}{LOGIN_PATH}")
            if probe.status_code == 401 or probe.status_code == 403:
                raise HandoffError(
                    "OTOBO's login page refused the request -- your site "
                    "session may have expired. Sign in again and retry.")
            if probe.status_code != 200:
                raise HandoffError(f"OTOBO's login page answered {probe.status_code}.")
            if PROBE_COOKIE not in client.cookies:
                raise HandoffError("OTOBO did not offer its usual cookie -- "
                                   "something about it may have changed.")

            # Step 2: the real login, now that the probe cookie is riding
            # along in the client's own jar automatically.
            r = client.post(f"{OTOBO_PUBLIC}{LOGIN_PATH}", data={
                "Action": "Login",
                "User": AGENT_LOGIN,
                "Password": AGENT_PASSWORD,
                "RequestedURL": "",
            })
    except httpx.RequestError as e:
        raise HandoffError(f"Could not reach OTOBO ({type(e).__name__}).")

    # OTOBO's own tell for "that password was wrong": a redirect-to-login
    # header on what is otherwise a plain 200. There is no clean success/
    # failure status code to check instead.
    if "X-OTOBO-Login" in r.headers:
        raise HandoffError(
            f"OTOBO rejected the {AGENT_LOGIN} account's password. If you "
            "changed it inside OTOBO directly, update MOM_OTOBO_SSO_PASSWORD "
            "in .env to match -- see OTOBO.md.")

    for raw in r.headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0].strip()
        if name not in (PROBE_COOKIE, SITE_COOKIE):
            return raw
    raise HandoffError("OTOBO answered but never issued a real session cookie.")
