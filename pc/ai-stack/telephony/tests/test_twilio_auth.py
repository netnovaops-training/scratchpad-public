"""
Tests for webhook verification.

These guard the endpoints that face the internet on teewithmsdee.example.org, so
the important cases here are the REFUSALS. A signature check that accidentally
passes everything looks exactly like one that works.

The reference values were generated with Twilio's own `RequestValidator` and
checked to match; that comparison is repeated below when the twilio package is
installed, and skipped when it isn't, so the suite runs either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.twilio_auth import expected_signature, signature_ok, stream_secret  # noqa: E402

TOKEN = "12345678901234567890123456789012"
HOST = "teewithmsdee.example.org"

SMS_PARAMS = {
    "Body": "BOOK",
    "From": "+17025551234",
    "To": "+17257264497",
    "MessageSid": "SM123",
}
SMS_SIG = "ITWw6HGWYfhzRpt2k9QrguTDd4Q="


def test_matches_known_signature():
    url = f"https://{HOST}/twiml/sms"
    assert expected_signature(TOKEN, url, SMS_PARAMS) == SMS_SIG


def test_accepts_a_valid_signature():
    assert signature_ok(TOKEN, HOST, "/twiml/sms", SMS_PARAMS, SMS_SIG)


def test_parameter_order_does_not_matter():
    """Twilio sorts by key; a dict in another order must produce the same result."""
    shuffled = dict(reversed(list(SMS_PARAMS.items())))
    assert signature_ok(TOKEN, HOST, "/twiml/sms", shuffled, SMS_SIG)


# --- the refusals ------------------------------------------------------------

def test_refuses_a_tampered_body():
    """
    The attack this exists to stop: same signature, different message. Without
    verification this would create a booking, or send a billed text.
    """
    tampered = dict(SMS_PARAMS, Body="RATE 5")
    assert not signature_ok(TOKEN, HOST, "/twiml/sms", tampered, SMS_SIG)


def test_refuses_a_spoofed_sender():
    spoofed = dict(SMS_PARAMS, From="+19995550000")
    assert not signature_ok(TOKEN, HOST, "/twiml/sms", spoofed, SMS_SIG)


def test_refuses_a_missing_header():
    assert not signature_ok(TOKEN, HOST, "/twiml/sms", SMS_PARAMS, None)
    assert not signature_ok(TOKEN, HOST, "/twiml/sms", SMS_PARAMS, "")


def test_refuses_when_the_auth_token_is_unset():
    """
    Fails CLOSED. The usual shortcut -- no credential configured, so skip the
    check -- would leave the endpoint wide open on the exact machine where
    someone forgot to fill in .env.
    """
    assert not signature_ok("", HOST, "/twiml/sms", SMS_PARAMS, SMS_SIG)


def test_refuses_when_public_host_is_unset():
    """Without PUBLIC_HOST the signed URL can't be rebuilt, so nothing can pass."""
    assert not signature_ok(TOKEN, "", "/twiml/sms", SMS_PARAMS, SMS_SIG)


def test_wrong_host_is_refused():
    """
    A signature for one hostname must not validate for another -- otherwise
    pointing the webhook somewhere else would go unnoticed.
    """
    assert not signature_ok(TOKEN, "someone-elses-host.example", "/twiml/sms", SMS_PARAMS, SMS_SIG)


def test_wrong_path_is_refused():
    """An /twiml/sms signature must not open /twiml/incoming."""
    assert not signature_ok(TOKEN, HOST, "/twiml/incoming", SMS_PARAMS, SMS_SIG)


def test_empty_params_still_verified():
    url = f"https://{HOST}/twiml/sms"
    sig = expected_signature(TOKEN, url, {})
    assert signature_ok(TOKEN, HOST, "/twiml/sms", {}, sig)
    assert not signature_ok(TOKEN, HOST, "/twiml/sms", {}, "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")


# --- the /media secret -------------------------------------------------------

def test_stream_secret_is_stable_and_derived():
    a = stream_secret(TOKEN)
    b = stream_secret(TOKEN)
    assert a == b, "must be stable across restarts or live calls would drop"
    assert len(a) == 32
    assert a != TOKEN, "must not be the auth token itself -- it travels in TwiML"


def test_stream_secret_changes_with_the_token():
    assert stream_secret(TOKEN) != stream_secret("a-different-token-entirely")


def test_stream_secret_is_empty_without_a_token():
    """
    Empty means /media refuses everything, because main.py treats an empty
    secret as 'no valid token possible'. Fail closed, again.
    """
    assert stream_secret("") == ""


def test_explicit_stream_secret_wins():
    assert stream_secret(TOKEN, "my-own-secret") == "my-own-secret"


# --- cross-check against Twilio's own implementation -------------------------

def test_agrees_with_twilios_validator():
    validator = pytest.importorskip(
        "twilio.request_validator", reason="twilio package not installed"
    )
    v = validator.RequestValidator(TOKEN)
    for path, params in (
        ("/twiml/sms", SMS_PARAMS),
        ("/twiml/incoming", {"From": "+17025551234", "CallSid": "CA99", "To": "+17257264497"}),
        ("/twiml/sms", {}),
    ):
        url = f"https://{HOST}{path}"
        assert expected_signature(TOKEN, url, params) == v.compute_signature(url, params)
