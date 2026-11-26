#!/usr/bin/env python3
"""dsm_api.py - minimal Synology DSM Web API client (login, call, logout). Never prints the sid or password.

Base URL: https://192.168.1.68:5001 (LAN; DSM self-signed cert, so verification is off for this LAN IP only).
Jason 11-24: if the LAN/Tailscale API is down, the QuickConnect host from the same Pass item may be used
(DSM_BASE=qc) for API calls only. Credentials go in the POST body, never a URL query.
Use:  from dsm_api import Dsm; d = Dsm(); d.login(); d.call("SYNO.Core.Terminal", "get", 3); d.logout()
      python dsm_api.py --check   -> login ok/fail code, DSM version, logout
OUTCOME 2026-11-24: written for the Synology Photos move (steps 3-7).
"""
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request

from creds import dsm_login

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class Dsm:
    """Session against one DSM; sid kept private."""

    def __init__(self):
        self.user, self._pw, qc = dsm_login()
        self.base = f"https://{qc}" if os.environ.get("DSM_BASE") == "qc" else os.environ.get(
            "DSM_BASE", "https://192.168.1.68:5001")
        self._sid = None

    def _post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(self.base + path, data=data)
        ctx = _CTX if "192.168." in self.base or "100.65." in self.base else ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def login(self):
        """Log in; returns (ok, error_code)."""
        j = self._post("/webapi/entry.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "login",
                                             "account": self.user, "passwd": self._pw, "session": "photosmove",
                                             "format": "sid"})
        if j.get("success"):
            self._sid = j["data"]["sid"]
            return True, None
        return False, (j.get("error") or {}).get("code")

    def call(self, api, method, version, **params):
        """Call an API method; returns the parsed JSON (sid stripped)."""
        f = {"api": api, "method": method, "version": str(version), "_sid": self._sid}
        f.update({k: (json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v)) for k, v in params.items()})
        return self._post("/webapi/entry.cgi", f)

    def logout(self):
        if self._sid:
            try:
                self._post("/webapi/entry.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "logout",
                                                 "session": "photosmove", "_sid": self._sid})
            finally:
                self._sid = None


if __name__ == "__main__":
    d = Dsm()
    ok, code = d.login()
    print("login", "ok" if ok else f"FAILED code {code}")
    if ok:
        info = d.call("SYNO.Core.System", "info", 1)
        dd = info.get("data", {})
        print("dsm", dd.get("firmware_ver"), "model", dd.get("model"))
        print("terminal", json.dumps(d.call("SYNO.Core.Terminal", "get", 3).get("data")))
        d.logout()
