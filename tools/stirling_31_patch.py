#!/usr/bin/env python3
"""stirling_31_patch.py - move Stirling-PDF to 3.1.0 with household SSO (2026-11-18).

Edits ~/nuc-homelab/docker-compose.yml in place (backup taken first by hand in
~/archive/2026-11-18/stirling/). Changes only the stirling-pdf service:
  - image: 2.14.3 digest -> stirlingtools/stirling-pdf 3.1.0 digest
  - SECURITY_ENABLELOGIN=false -> login on, OAuth2 via the household issuer (/auth/oidc),
    the same values that worked 2026-09-04 (git 95304c8), plus 3.x's ssoAutoLogin so a
    signed-in browser never sees a login page.
Why now: 3.0 made OAuth/OIDC SSO free ("OAuth2 / OIDC works on every plan", docs.stirlingpdf.com,
Server/Security/OAuth-SSO); 2.14 charged for it, which is why login was off since 10-14.
OUTCOME: see the Review line on the Stirling to-do item.
"""
from pathlib import Path

f = Path.home() / "nuc-homelab/docker-compose.yml"
t = f.read_text(encoding="utf-8")
old_img = "image: frooodle/s-pdf@sha256:3b3670fce70b396ec56ba380a3cc7858e0abf83fe13f31c88f7737847763a396"
new_img = ("# 2026-11-18: 3.1.0 (OAuth SSO free since 3.0). Rollback: the frooodle/s-pdf 2.14.3 digest +\n"
           "    # ~/archive/2026-11-18/stirling/stirling_config-2.14.3.tgz (the DB upgrades in place).\n"
           "    image: stirlingtools/stirling-pdf@sha256:b5b9e400c086e5334a4947a0d7cd589808f743f29267d7f41c18200cf4bc48b4")
old_env = "      - SECURITY_ENABLELOGIN=false\n"
new_env = """      # LOGIN BACK ON 2026-11-18 with 3.1.0, SSO only (Jason: "use my sso, no new passwords").
      # loginMethod oauth2 = no local password form at all; ssoAutoLogin skips the button, and the
      # household issuer reads the gate cookie, so a signed-in browser lands straight in /pdf.
      # Free plan = 5 users (docs Plans-and-Licences); per-person access is still decided on /admin/people.
      - SECURITY_ENABLELOGIN=true
      - SECURITY_LOGINMETHOD=oauth2
      - SECURITY_SSOAUTOLOGIN=true
      - SECURITY_OAUTH2_ENABLED=true
      - SECURITY_OAUTH2_AUTOCREATEUSER=true
      - SECURITY_OAUTH2_PROVIDER=household
      - SECURITY_OAUTH2_CLIENT_NAME=household
      - SECURITY_OAUTH2_ISSUER=https://example.org/auth/oidc
      - SECURITY_OAUTH2_CLIENTID=stirling-pdf
      - SECURITY_OAUTH2_SCOPES=openid,profile,email
      - SECURITY_OAUTH2_USEASUSERNAME=email
"""
assert t.count(old_img) == 1 and t.count(old_env) == 1, "compose not as expected; nothing changed"
t = t.replace(old_img, new_img).replace(old_env, new_env)
f.write_text(t, encoding="utf-8")
print("patched")
