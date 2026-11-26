#!/usr/bin/env python3
"""dsm_web.py - Dsm session like the DSM web UI (session "webui" + SynoToken header). Some DSM 7.2 writes
(user create, share create) answer 105/3300 on a plain sid session. Never prints the sid, token or password.
OUTCOME 2026-11-24: written for the abyss-trim NAS user (abyss_trim_setup.py)."""
import json
import urllib.parse
import urllib.request

from dsm_api import Dsm, _CTX


class DsmWeb(Dsm):
    tok = None

    def _post(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(self.base + path, data=data)
        if self.tok:
            req.add_header("X-SYNO-TOKEN", self.tok)
        with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def login(self):
        j = self._post("/webapi/entry.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "login",
                                             "account": self.user, "passwd": self._pw, "session": "webui",
                                             "format": "sid", "enable_syno_token": "yes"})
        if j.get("success"):
            self._sid = j["data"]["sid"]
            self.tok = j["data"].get("synotoken")
            return True, None
        return False, (j.get("error") or {}).get("code")

    def logout(self):
        if self._sid:
            try:
                self._post("/webapi/entry.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "logout",
                                                 "session": "webui", "_sid": self._sid})
            finally:
                self._sid = self.tok = None
