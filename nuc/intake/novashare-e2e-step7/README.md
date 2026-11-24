# Novashare step 7: one strict CSP for the whole drop site, plus the audit fixes (2026-11-24)

**Live since 2026-11-24 ~09:13.** Audit: `../../AUDIT-2026-11-24.md` (High 2a, Medium 8a and 8, Low 21-25, the
Novashare UI items, the intake ruff/eslint items). Jason's calls: "approve the fix batch", then "keep the old name and
burn the old pages", so there is **no `e2e.` subdomain**: the key pages stay on `drop.home.example.net` and no
device has to be set up again.

## What changed

- **Burned** (moved out of the live folder into `~/archive/2026-11-24/e2e/burned/`): `portal.py.bak-20261119` (an old
  Drop page with inline script), the one-time patchers that wrote the old inline-script pages (`atomic_patch.py`,
  `captcha_patch.py`, `gemini_patch.py`, `gotenberg_web_patch.py`, `pwa_patch.py`, `share_patch.py`, `pwa_sbx.sh`) and
  `gemini_probe.py`. None was imported by the app. No served page was removed: every page is still needed.
- **Rebuilt without inline script or style:** the Drop page `/`, `/sending`, `/routes` and the share result page. Their
  CSS and JS are files at `/assets/<name>` (`site_assets.py`) loaded with SRI. The theme pictures' own `<style>` and
  `<script>` moved into those files (`themes.svg_parts`). Service workers are registered through a Trusted Types policy
  that allows only `/sw.js`. The key pages (Devices, Send, Inbox, share picker, keyring, push) were already strict.
- **One header set on every answer** (`app.site_headers`): `site_assets.SITE_CSP` = `default-src 'none'; script-src
  'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; worker-src
  'self'; frame-src 'self'; media-src 'self' blob:; form-action 'self'; base-uri 'none'; object-src 'none';
  frame-ancestors 'none'; require-trusted-types-for 'script'`, plus CORP same-origin, COOP same-origin, nosniff,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a Permissions-Policy. Pages that set their own CSP keep it
  (equal or stricter). **Exceptions, on purpose:** `/keyring` has COOP `unsafe-none` (Abyss `/album/` opens it as a
  popup and must keep its handle to get the answer; framing is still forbidden) and `/relay/share` has COOP
  `same-origin-allow-popups` (it opens Abyss for "Send as Abyss"). Caddy's drop site fills in the same set on its own
  answers (`?` headers; `/__sso*` excluded).
- **ML-KEM secret** (High 2a): sealed by a non-extractable AES-GCM key (`noble2`); the old AES-KW wrap of an
  EXTRACTABLE HMAC carrier is gone. Old records are re-sealed on the next Devices visit. Native ML-KEM (no secret in
  page memory at all) is still preferred, and the Devices page suggests "Make new keys" when a device could move to it.
- **8a:** E2E routes accept the SSO headers only from the Caddy edge (`INTAKE_EDGE_PEERS`, default `127.0.0.1,192.168.1.86`),
  never from a service peer.
- **22:** content commitment: root of all piece ciphertext hashes in the manifest (v2), SHA-256(manifest) in every wrap
  context (`novashare/wrap/v2`). The receiver checks both. Server unchanged.
- **23:** re-key = `X-Device-Id` of that device + a one-time challenge answered with its current X25519 key.
- **24:** `using()` only after the access check. **25:** verified / not verified per person, "The numbers match",
  "Only send to people I have verified".
- **UI:** two-tap Cancel / Decline announce their armed state; the inbox is no longer one big live region.
- **Image:** `python:3.12.16-slim@sha256:a0e2dd78...`, rebuilt `--pull`. Trivy HIGH 140 -> 83, fixable 57 -> 0.
- **Lint:** eslint 0 problems on every served script; ruff F/E9 clean; link_probe.py ASYNC230 fixed.

## Files

`site_assets.py`, `test_harden.py`, `test_harden_browser.py`, `fake_abyss.html` (new); the changed live files; the
patcher `novashare_harden_patch.py` (refuses if a live file changed since 09:00), `Dockerfile.new`, `harden_sbx.sh`
(`test | regress | up | down`), `harden_swap.sh` (the swap), `trivy_count.sh`.

## Tests (2026-11-24)

- `harden_sbx.sh test`: every self-contained suite in the patched image vs the live image. All that pass on live pass
  here: test_harden 21, test_devices 86 (re-key now needs the proof), test_relay 100, test_receive 80, test_push 84,
  test_pick 102, test_sweep 82, test_keyring 16, test_abyss_handoff 15, test_ui 22 and the library suites. 8 suites
  fail on live and patched alike (need mounts or services): test_purple, test_hzicon, test_onion, test_torlink,
  test_t0958, test_t0964b, test_libraries, test_themes. `node --check` on all 11 served scripts.
- `harden_sbx.sh regress` (intake_regress_live.sh on the patched copy): REGRESSION ALL PASS.
- `test_harden_browser.py` (two Chrome profiles, Caddy front door): ALL PASS - strict headers on every page, 0 CSP or
  Trusted Types violations, device records sealed (native and forced-vendored), verified people and the strict
  setting, a 9 MB send saved byte-identical, the Abyss album keyring popup wraps and opens a key, the re-key proof
  works in the browser and old album wraps stop opening after it.
- Not tested in a browser: the legacy `noble` -> `noble2` re-seal (live has one device; Chrome made it native).
  test_relay_browser.py / relay_twin.py from step 2 still speak wrap v1 and are historic.

## Undo

Printed by `harden_swap.sh`: copy back `~/archive/2026-11-24/e2e/*.py`, `Dockerfile`, `burned/*`, `Caddyfile.nuc`,
reload Caddy, rebuild; or retag `nuc-homelab-intake:before-e2e`.
