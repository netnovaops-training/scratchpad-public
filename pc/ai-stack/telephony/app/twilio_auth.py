"""
Proving a request really came from Twilio.

WHY THIS EXISTS
===============
Until now these endpoints trusted anyone who could reach them. That was
survivable only because they weren't routed anywhere public. The moment Caddy
serves `/twiml/*` and `/media` on a real domain, "anyone who can reach them"
means the internet, and the three endpoints do very different amounts of damage:

  /twiml/sms  -- the worst. POST a form with `Body=BOOK&From=+1555...` and you
                 drive the whole keyword handler as if you were a client:
                 create client codes, leave notes, submit ratings. Once
                 SMS_ENABLED is true it will also send REAL texts, billed to
                 the account, to any number the attacker names.
  /media      -- open a WebSocket and you get a free conversation with the
                 model on his GPU, with the business's system prompt.
  /twiml/incoming -- less damaging, but it leaks that the line exists and lets
                 someone fill the logs.

Twilio signs every webhook. Verifying that signature is the whole fix, and it
costs a hash.

HOW THE SIGNATURE WORKS
=======================
Twilio takes the exact URL it requested, appends every POST parameter as
key+value in *alphabetical order by key*, HMAC-SHA1s that with your auth token,
and base64s the result into `X-Twilio-Signature`. We do the same and compare.

THE PROXY GOTCHA
================
The URL has to match byte for byte. Behind Caddy the app sees
`http://telephony:8093/twiml/sms` while Twilio signed
`https://teewithmsdee.example.org/twiml/sms`. Reconstructing from request headers
is possible but fiddly and easy to get subtly wrong.

So we don't. We rebuild the URL from PUBLIC_HOST, which is by definition the
hostname you gave Twilio. If those two disagree, the signature fails -- which
is the correct outcome, because it means the webhook is pointed somewhere the
app doesn't know about.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

log = logging.getLogger("telephony.auth")


def expected_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's algorithm, exactly."""
    payload = url
    for key in sorted(params):
        payload += key + str(params[key])
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def signature_ok(
    auth_token: str,
    public_host: str,
    path: str,
    params: dict[str, str],
    header: str | None,
) -> bool:
    """
    True if `header` is a valid Twilio signature for this request.

    Returns False when the auth token isn't configured. That's deliberate and
    it is the opposite of the usual "no credential means skip the check"
    shortcut -- an unconfigured token would otherwise mean an open endpoint,
    which is exactly the state this module exists to prevent.
    """
    if not auth_token:
        log.error(
            "TWILIO_AUTH_TOKEN is not set, so no webhook can be verified. "
            "Refusing the request. Set it in .env -- Twilio Console > Account Info."
        )
        return False

    if not header:
        log.warning("request had no X-Twilio-Signature header -- refused")
        return False

    if not public_host:
        log.error("PUBLIC_HOST is not set, so the signed URL can't be rebuilt -- refused")
        return False

    url = f"https://{public_host}{path}"
    expected = expected_signature(auth_token, url, params)

    # compare_digest, not ==. String comparison returns early on the first
    # differing byte, and that timing difference is enough to recover a
    # signature one character at a time given enough attempts.
    if hmac.compare_digest(expected, header):
        return True

    log.warning(
        "signature mismatch for %s -- if this is a real Twilio call, check that "
        "the webhook URL in the Twilio Console is exactly https://%s%s",
        path, public_host, path,
    )
    return False


def stream_secret(auth_token: str, explicit: str = "") -> str:
    """
    The shared secret that proves a WebSocket connection came from our own TwiML.

    Twilio cannot sign a WebSocket handshake -- there's no header to put a
    signature in -- so `/media` can't be protected the same way. Instead the
    TwiML we generate carries a secret as a <Parameter>, and it comes back in
    the stream's `start` event. Only something that fetched our signed TwiML
    could know it.

    Derived from the auth token by default so there's nothing extra to
    configure and nothing extra to forget. Set PHONE_STREAM_SECRET in .env to
    override.
    """
    if explicit:
        return explicit
    if not auth_token:
        return ""
    return hashlib.sha256(("media-stream:" + auth_token).encode("utf-8")).hexdigest()[:32]
