# Novashare E2E: threat model (2026-11-23, Step 0)

Made by the soundcheck threat-modeling agent from NOVASHARE-E2E-PLAN.md and the intake code. This is a design-stage
model: nothing here was tested live. The agent had no write tool, so this file holds its ranked findings as
returned (full text in the session log).

## The headline
The NUC serves the JavaScript and the service worker that do the encryption. A compromised NUC, or an XSS on the
drop origin, gets around every crypto control. "The NUC never sees a file" is true against a server that is breached
afterwards (disk, backups, a passive reader), not against an active malicious server. The design must say so.

## Problems in today's code that the E2E path must not inherit
- **Plaintext fallback.** If the IndexedDB save fails, the service worker sends the share the old way:
  atomic_ui.py:63-65 does `fetch(spare)`, which posts plaintext to /share.
- **File names reach the server.** resumable.py takes the name at /up/start, builds the dedupe key from it, stores
  it in meta and writes it to WHY.txt (lines 13, 288, 337, 90-101).
- **No Content-Security-Policy** anywhere, and /sending uses an inline script (atomic_ui.py:281).

## Ranked threats and the decisions they force (L = likelihood, I = impact)

| # | Threat | L / I | Design decision |
|---|---|---|---|
| 1 | Server ships changed JS or service worker | low-med / critical | Separate E2E origin, immutable hashed bundles with SRI, code version shown in the UI. Limit written down. |
| 2 | XSS or an extension gets the keys (the noble ML-KEM secret has to sit in the JS heap) | med / critical | Own origin `e2e.` (IndexedDB isolated); strict CSP with no inline script and Trusted Types; optional WebAuthn PRF unlock. |
| 3 | Key substitution, or a planted NEW device | low-med / high | Per-person identity key; a new device must be approved by an existing device (QR code) and signed; clients pin keys and alert on any new or changed device; safety number over identity keys, compared by QR. |
| 4 | No sender authentication: the server could inject a file "from Casey" | med / high | Ephemeral X25519 + ML-KEM per wrap through a vetted combiner (X-Wing / HPKE hybrid, not hand-rolled HKDF); the manifest is signed by the sender's device, certified by its identity key. |
| 5 | Library path confused with E2E; plaintext fallback | med-high / high | Destination chosen on the device before any request; no network fallback for E2E; library choice looks different and needs a confirm; separate /relay routes. |
| 6 | Metadata leaks (who, sizes, times; names through resumable) | certain / medium | A relay engine with no names; size buckets; logs with ids only; opaque push (the device builds the text). |
| 7 | Downgrade to non-PQ | low-med / high (long-term) | No classical-only path; device records carry both keys + version, signed; algorithm id bound into the derivation; check TLS uses X25519MLKEM768. |
| 8 | Truncation, replay, reorder | low / medium | Decrypt to a temp file, finalize only after the last piece and the manifest totals check out; signed manifest with transfer id + time; seen-id set. |
| 9 | Lost or stolen phone, revocation | med / high | Revoke ends the device's SSO session and push; removal needs another device or the admin; rotate after revocation. |
| 10 | No forward secrecy, long-kept ciphertext | low-med / high | Ephemeral sender keys; rotated recipient KEM keys (signed prekeys); delete ciphertext once accepted; relay store kept out of backups. |

Also covered:
- Server withholding or deleting items: cannot be prevented, so it is detected with signed receipts.
- VAPID key theft: fake notifications only, no decryption.
- Same-site CSRF from sibling subdomains: require `Sec-Fetch-Site: same-origin` and a custom header, with no
  service-peer exemptions on /devices and /relay.
- A household member as attacker: IDOR checks, quotas, and view-level users cannot register devices.
- Supply chain: noble pinned, integrity-checked and SRI-served, and checked against FIPS 203 test vectors.

## Residual risks to accept
- An active malicious NUC can defeat web-delivered E2E.
- The server sees who-talks-to-whom.
- The server can deny service.
- Files already received cannot be recalled.
