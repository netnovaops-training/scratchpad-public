# Component Design: Novashare E2E relay (`/relay/*` and `/devices/*` in intake, served on its own origin)

## Change index (threat-model decisions folded in)
- [CHG-a] Own origin `e2e.home.example.net`, strict CSP, Trusted Types, SRI on the vendored `mlkem768.min.js`.
- [CHG-b] Per-person Ed25519 identity key signs each device record. A new device must be approved by an existing device of the same person. Clients pin and alert on any new or changed device. Device routes move to `/devices/*`. Schema: `person_identity`, `device` columns, `device_prekey`.
- [CHG-c] Per-wrap hybrid via X-Wing (ephemeral X25519 + ML-KEM-768) with all ciphertexts, public keys, algorithm id, transfer id and device ids bound, plus the sender-static term. The wrap table and KDF change.
- [CHG-d] The manifest is signed by the sender device (a per-device Ed25519 signing key is added).
- [CHG-e] No plaintext fallback. Push payload is opaque (`{"t": id}`). The device builds the notification text after decrypting the manifest.
- [CHG-f] Receivers decrypt to a temp file and finalize only after the last piece and the manifest totals verify.
- [CHG-g] Sec-Fetch-Site must be `same-origin` plus a custom header on `/relay` and `/devices`. Relay routes reject service-key callers.
- [CHG-h] Revoke also kills the push subscription. Removal needs another device of the same person, or an admin.
- [CHG-i] Monthly rotation of recipient X-Wing prekeys (signed-prekey style). The relay store is excluded from backups, which drops my earlier nightly `.backup` idea.

## 1. Context
- **Type:** HTTP endpoints added to the existing FastAPI app, plus two in-process background tasks (sweep every 10 min, push sender) and an outbound Web Push integration.
- **Placement:** the app runs as one uvicorn process on the NUC in Docker, behind Caddy forward_auth. The person comes from `X-Auth-Email` via `app._person` (`app.py:631`, backed by `dropconfig.WHO`). If the config fails to load, WHO is empty and every relay call gets 403 `not_in_household`, the same fail-closed behaviour as `dropconfig.py:166`.
- **Latency:** the API calls are small and run in under 100 ms. Piece PUT and blob GET are streaming and long-lived.
- **Failure model:** the clients are PWAs that retry on their own, so every call must be replay-safe.
- **Concurrency model:**
  - One process, so in-memory guards are the truth, as in `_FINISHING` (`resumable.py:524`).
  - One SQLite writer lock.
  - `rescan_held.py` imports app every hour in a second process. Importing `relay`, `relaydb` or `push` must have no side effects: no DB open, no schema migration, no file creation. All of that happens in `_lifespan` (`app.py:101`), which only uvicorn runs. The second process never opens `relay.db`.
- **Gatekeeper behaviour (`app.py:215-258`):**
  - Writes need `X-Auth-Level: full`, so view-only people cannot register, send, accept or decline.
  - The gatekeeper refuses only cross-site writes. Its body-size and free-space check covers only `/intake`, `/upload` and `/share`.
  - A caller with a valid `X-Drop-Key` and no level passes the gatekeeper on reads, and on `/upload` and `/intake` writes. Relay routes must not honour that path (see section 4).
- **[CHG-a] Own origin:**
  - The E2E client runs on `e2e.home.example.net`, a new Caddy site block that proxies to the same intake container. It is a subdomain behind Caddy, so the "no new ports" rule holds.
  - The site block uses the same forward_auth and the same header handling as the drop site (strip inbound `X-Auth-Email`, set it from `/verify`, add `X-Drop-Key`).
  - It proxies only an allowlist of paths: `/relay/*`, `/devices/*`, `/e2e/*` (static client files) and `/sw-e2e.js`, `/e2e.webmanifest`, `/healthz` is not exposed. The old drop host does not proxy `/relay/*` or `/devices/*` (404 at Caddy).
  - The reason for the split: the drop host's pages (atomic_ui.py, portal) use inline script, so keys and plaintext must never live in that origin. IndexedDB is per origin, so the E2E keys exist only on the e2e origin.
  - **Strict CSP on every e2e response (set by Caddy, asserted by a test):** `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types novashare`. No inline script, no inline event handlers, no `eval`.
  - The page code creates one Trusted Types policy named `novashare` that only passes constants, and uses `textContent` for all user data.
  - Also set `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`, `Permissions-Policy: camera=(self)` (QR scan only) and `Cache-Control: no-store` on API responses.
  - The vendored `mlkem768.min.js` (and the app bundle) load with `<script type="module" src integrity="sha384-...">`. The file name carries the version and hash. A CI test fails if the served bytes differ from the hash recorded in the repo.
  - Known gap: service workers cannot use SRI. `sw-e2e.js` is a single built bundle with the library inlined, named by content hash, and checked by the same CI test.
- **Not added:** no new service, DB, port or open port. Caddy's `request_body max_size 11000MB` must be copied to the e2e block.

## 2. Data model
- **Entities:** person_identity, device, device_prekey, push_sub, transfer, transfer_recipient, wrap, delivery, piece, audit, counter.
- **Ownership:** an identity belongs to one person. A device belongs to one person and is certified by that identity. A transfer belongs to its sender device. Wrap, delivery and piece rows cascade-delete with the transfer.
- **Lifecycle:** transfer rows live 30 days past their terminal state as tombstones. Ciphertext is deleted at the terminal state.
- **[CHG-i] Backups:** `STAGE/relay.db` (with `-wal` and `-shm`) and `STAGE/relay/` are excluded from every backup job. The NUC backup configuration was not read, so excluding STAGE and verifying it is a step-1 task. Consequence: restoring the NUC does not bring back the relay. Recovery is re-enrolment: clients keep their keys in IndexedDB, re-register, and re-pin by fingerprint. This costs nothing in the normal case and avoids ciphertext, push endpoints and metadata sitting in backups.

### Schema (`PRAGMA journal_mode=WAL; foreign_keys=ON; synchronous=FULL; busy_timeout=5000; user_version` per step)

**[CHG-b] Identity and devices:**
```sql
CREATE TABLE person_identity(person TEXT PRIMARY KEY,
 ed25519_pub BLOB NOT NULL CHECK(length(ed25519_pub)=32), created INTEGER NOT NULL);
-- trigger: BEFORE UPDATE raises ABORT (a key never changes; an admin reset deletes the row)
CREATE TABLE device(id TEXT PRIMARY KEY CHECK(length(id)=22),     -- token_urlsafe(16), server-issued
 person TEXT NOT NULL REFERENCES person_identity(person),
 label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 40),
 x25519_pub BLOB NOT NULL CHECK(length(x25519_pub)=32),           -- static, WebCrypto non-extractable
 sign_pub BLOB NOT NULL CHECK(length(sign_pub)=32),               -- Ed25519, signs manifests and prekeys
 fingerprint TEXT NOT NULL, record_sig BLOB CHECK(record_sig IS NULL OR length(record_sig)=64),
 identity_bundle BLOB CHECK(identity_bundle IS NULL OR length(identity_bundle)<=2048),
 created INTEGER NOT NULL, approved_at INTEGER, approved_by TEXT, last_seen INTEGER NOT NULL,
 revoked_at INTEGER, revoked_by TEXT, UNIQUE(person, fingerprint),
 CHECK(approved_at IS NULL OR record_sig IS NOT NULL));
CREATE INDEX device_live ON device(person) WHERE revoked_at IS NULL AND approved_at IS NOT NULL;
```
Triggers on `device`:
- `BEFORE UPDATE OF id, person, x25519_pub, sign_pub, fingerprint, created` raises ABORT. A new key means a new device.
- `record_sig` and `approved_at` are set-once.
- `revoked_at` cannot be cleared.

`record_sig` is the person identity key's signature over the canonical device record: domain string `novashare/device/v1`, person id, device id, label, `x25519_pub`, `sign_pub` and `created`, each length-prefixed. `fingerprint` is the SHA-256 of that record without the signature.

**[CHG-i] Prekeys (rotated monthly):**
```sql
CREATE TABLE device_prekey(device_id TEXT NOT NULL REFERENCES device(id) ON DELETE CASCADE,
 id INTEGER NOT NULL, xwing_pub BLOB NOT NULL CHECK(length(xwing_pub)=1216),  -- 1184 ML-KEM + 32 X25519
 sig BLOB NOT NULL CHECK(length(sig)=64), created INTEGER NOT NULL, retired_at INTEGER,
 PRIMARY KEY(device_id, id)) WITHOUT ROWID;
CREATE UNIQUE INDEX prekey_live ON device_prekey(device_id) WHERE retired_at IS NULL;  -- one live prekey per device
```
`sig` is the device's Ed25519 signature over `novashare/prekey/v1 || device_id || id || xwing_pub`. The device key columns in the first design (`mlkem768_pub`) are gone: ML-KEM now lives only in the prekey.

**Push and transfers:**
```sql
CREATE TABLE push_sub(device_id TEXT PRIMARY KEY REFERENCES device(id) ON DELETE CASCADE,
 endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
 created INTEGER NOT NULL, fail_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE transfer(id TEXT PRIMARY KEY CHECK(length(id)=22),
 sender_person TEXT NOT NULL, sender_device TEXT NOT NULL REFERENCES device(id),
 start_key TEXT NOT NULL, body_hash BLOB NOT NULL, alg TEXT NOT NULL CHECK(alg='novashare-v1'),
 size_ct INTEGER NOT NULL CHECK(size_ct>0), piece_size INTEGER NOT NULL CHECK(piece_size=4194304),
 pieces INTEGER NOT NULL CHECK(pieces=(size_ct+piece_size+15)/(piece_size+16)),
 manifest BLOB NOT NULL CHECK(length(manifest) BETWEEN 29 AND 65536),
 manifest_sig BLOB NOT NULL CHECK(length(manifest_sig)=64),               -- [CHG-d]
 state TEXT NOT NULL CHECK(state IN('uploading','ready','cancelled','expired','deleted')),
 created INTEGER NOT NULL, last_activity INTEGER NOT NULL, ready_at INTEGER,
 expires_at INTEGER NOT NULL, UNIQUE(sender_device, start_key));
CREATE INDEX transfer_sweep ON transfer(state, expires_at);
CREATE TABLE transfer_recipient(transfer_id TEXT REFERENCES transfer(id) ON DELETE CASCADE,
 person TEXT NOT NULL, PRIMARY KEY(transfer_id, person));
```
`pieces` is the ceiling of `size_ct / (piece_size + 16)`. Every ciphertext piece is 4 MiB + 16 bytes except the last.

**[CHG-c] Wrap, delivery, piece:**
```sql
CREATE TABLE wrap(transfer_id TEXT REFERENCES transfer(id) ON DELETE CASCADE,
 device_id TEXT NOT NULL, prekey_id INTEGER NOT NULL,
 kem_ct BLOB NOT NULL CHECK(length(kem_ct)=1120),     -- X-Wing ct: 1088 ML-KEM + 32 ephemeral X25519
 wrapped_key BLOB NOT NULL CHECK(length(wrapped_key)=60),   -- 12 nonce + 32 key + 16 tag
 PRIMARY KEY(transfer_id, device_id),
 FOREIGN KEY(device_id, prekey_id) REFERENCES device_prekey(device_id, id)) WITHOUT ROWID;
```
```sql
CREATE TABLE delivery(transfer_id TEXT NOT NULL, device_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('pending','accepted','downloaded','declined')),
 updated INTEGER NOT NULL, push_claimed_at INTEGER, pushed_at INTEGER,
 push_attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(transfer_id, device_id),
 FOREIGN KEY(transfer_id, device_id) REFERENCES wrap(transfer_id, device_id) ON DELETE CASCADE) WITHOUT ROWID;
CREATE INDEX delivery_inbox ON delivery(device_id, state);
CREATE INDEX delivery_push ON delivery(pushed_at) WHERE pushed_at IS NULL;
CREATE TABLE piece(transfer_id TEXT REFERENCES transfer(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
 sha256 BLOB NOT NULL CHECK(length(sha256)=32), PRIMARY KEY(transfer_id, idx)) WITHOUT ROWID;
CREATE TABLE audit(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL,
 person TEXT, device_id TEXT, transfer_id TEXT);   -- ids and times only; trimmed at 90 days
CREATE TABLE counter(kind TEXT PRIMARY KEY, n INTEGER NOT NULL);   -- bumped inside the transition's transaction
```

### Invariants and where they are enforced
- **Keys immutable, certified, no revival:** the triggers above, and `approved_at` requires `record_sig`.
- **One identity per person, never changed in place:** primary key plus the update trigger. First-device bootstrap is insert-once, and a concurrent second first-device fails the insert and becomes a pending device (decision 17).
- **One live prekey per device:** the partial unique index.
- **Wraps only to a device's live prekey:** checked in the create transaction (409 `prekey_changed`).
- **Piece arithmetic:** the `pieces` CHECK, plus an exact expected length per piece index in the app.
- **No plaintext or names on the server:** only the opaque manifest ciphertext (capped at 64 KiB). A test greps for a canary.
- **One wrap and one delivery per device per transfer:** composite primary keys.
- **Wrap set equals the live approved device set of the targets at create time:** checked inside the create transaction.
- **A transfer cannot go `ready` unless `count(piece) = pieces`:** same transaction as the last piece insert.
- **Server-side signature checks (defence in depth, not the trust anchor):** identity signature on device records at registration and approval, device signature on prekeys, sender device signature on the manifest. Python `cryptography` is already pulled in by pywebpush. Clients re-verify the whole chain themselves.

### Queries and access paths
- **Inbox:** `delivery_inbox` joined to `transfer` by primary key.
- **Missed list:** `transfer_recipient(person)` joined to `transfer` where `created < device.approved_at` and no delivery exists for this device. Add the index `transfer_recipient(person)`.
- **Directory:** `device_live` plus the live prekey via `prekey_live`.
- **Quota sums:** the partial index `transfer(sender_person) WHERE state IN ('uploading','ready')`.
- **Sweep:** `transfer_sweep`.
- **Push work:** `delivery_push`.

### Files
- `STAGE/relay/<id>/data` is created with `posix_fallocate`, falling back to sparse on `EOPNOTSUPP/EINVAL`, like `resumable.py:318-332`. STAGE's top-level `*.json`, `*.part` and `*.tmp` sweeps do not match it.
- Ids are validated by the same normpath and regex wall as `resumable._staged` (`resumable.py:151`).

### Wire formats and crypto rules (client side)
- **Content key and pieces:** a random AES-256-GCM content key per transfer. Plaintext pieces of 4 MiB. Nonce = per-transfer random 8-byte prefix plus the 4-byte piece index. AAD = `transfer_id || index || last-flag`. The prefix is chosen once per transfer, so a page reload re-encrypts a piece to identical bytes and resume stays idempotent by sha256. The client persists key, prefix and `start_key` in IndexedDB until the transfer ends.
- **[CHG-c] Wrap per recipient device:**
  - The sender encapsulates to the recipient's live X-Wing prekey. X-Wing is ML-KEM-768 plus an ephemeral X25519 combined with a SHA3-256 combiner that already binds the X25519 ciphertext and public key. The vendored bundle `mlkem768.min.js` must expose it (noble's `ml_kem768_x25519`). If the built bundle has only plain ML-KEM, the X-Wing combiner is implemented in the bundle with the same inputs. Either way the owner should confirm the exact bundle contents (decision 19).
  - Then the sender-static term is added: `ss_static = X25519(sender_static_priv, recipient_static_pub)`. `sender_static` is the sender device's `x25519_pub`; `recipient_static` is the recipient device's `x25519_pub`.
  - `wrap_key = HKDF-SHA256(ikm = ss_xwing || ss_static, salt = 32 zero bytes, info = ctx)`.
  - `ctx` is length-prefixed: `"novashare/wrap/v1"`, alg id `novashare-v1`, transfer id, sender device id, recipient device id, prekey id, sender static pub, recipient static pub, recipient prekey `xwing_pub`, `kem_ct`.
  - `wrapped_key` is AES-256-GCM of the content key under `wrap_key`, with a random 12-byte nonce and AAD = `ctx`.
  - The sender-static term gives sender authenticity, so a hacked server cannot forge a wrap "from Casey" without Casey's static device key.
- **[CHG-d] Manifest:**
  - The plaintext holds the file list: name, type, size, offset and per-file SHA-256, plus total plaintext bytes and file count, and an optional caption.
  - Encrypted with AES-GCM under the content key, a random 12-byte nonce, AAD `transfer_id || "manifest"`.
  - The sender device signs a transcript with its `sign_pub` key: `novashare/transfer/v1`, alg id, transfer id, sender device id, `size_ct`, `piece_size`, `pieces`, SHA-256 of the manifest ciphertext, and the sorted list of `(recipient device id, prekey id)` pairs.
  - Stored as `manifest_sig`. Recipients verify the signature against the sender device record before they trust or decrypt anything.
- **[CHG-i] Prekey secrets:** the X-Wing secret (a 32-byte seed) cannot be a non-extractable CryptoKey, because it is not a native algorithm. It is stored in IndexedDB encrypted under a non-extractable AES-GCM key (this replaces "AES-KW"; either works, the point is that page script can use but not export the wrapping key). The static X25519 and Ed25519 signing keys use WebCrypto non-extractable keys where the browser supports them, with the vendored noble implementation as a fallback (decision 20).

## 3. Failure modes
| Failure | Detect | Surface | Recover |
|---|---|---|---|
| Recipient person has no live approved devices | create-time device query | 422 `no_devices` with the person ids | The sender tells them to set up Novashare. No transfer row is created. |
| Device set or prekey changed between listing and create | wrap set or prekey ids differ inside the transaction | 409 `devices_changed` or 409 `prekey_changed` with the fresh directory | The client re-wraps (cheap) and retries. |
| Device registered or approved after send | `approved_at > transfer.created` | Inbox "missed" tombstone, reason `before_device_setup` | None by design. The sender can resend. |
| Sender disconnects or the page reloads | `GET /relay/transfers/{id}` | `missing: [...]` | Same `start_key` returns the same transfer (`resumed: true`). |
| Server restart mid-upload | piece rows survive in SQLite | none | Resume from piece rows. There are no finish locks to clear. |
| Piece corrupted or cut short | length and sha256 check before pwrite | 422 `checksum_mismatch` or 400 `piece_short` | The client re-sends. |
| Piece re-sent with different bytes | row exists with a different sha | 409 `piece_conflict` | Cannot happen with deterministic nonces. Surfaced as a client bug. |
| Disk full or reserve breached | fallocate ENOSPC, or `free < size + RESERVE` (`resumable.py:63`) | 507 `no_space`; ENOSPC on a piece is also 507 | The transaction is rolled back and the file removed. The client retries later. |
| Relay total cap hit | sum of open `size_ct` | 507 `relay_full` | Deliveries drain or expire. |
| Quota or rate limit | sums inside the create transaction, in-memory token bucket | 429 `quota_bytes`, `quota_count` or `rate_limited` with Retry-After | Wait or cancel an upload. |
| **[CHG-f]** Ciphertext tampered or truncated, or manifest totals disagree | client AEAD failure, missing last-flag piece, or total or per-file SHA-256 mismatch after the last piece | The client discards the temp file, shows "could not verify, not saved", and does not call complete | The delivery stays `accepted`. The client retries the download. |
| **[CHG-d]** Manifest or wrap forged or swapped by the server | signature or chain check fails on the client | Client shows "this item could not be verified" and refuses to decrypt | The sender resends. The event is logged on the client only. |
| Push service down | send exception or 5xx | none | `push_attempts++`, exponential backoff up to 5 attempts. The inbox still works. |
| Push 404/410 | response code | none | Delete the `push_sub` row. |
| Push endpoint is malicious (SSRF) | host not in allowlist, or resolves to a private IP | 400 `bad_push_endpoint` | Refused at subscription time and again at send time. |
| **[CHG-e]** Push arrives and the device cannot build text (session expired, decrypt failure) | service worker fetch fails | Generic notification "New Novashare item" (a visible notification is required) | The user opens the app, which re-authenticates. |
| Expiry vs running download | active reader count under the per-transfer guard | none | The sweep skips and retries next run. An unlinked file stays readable to an open stream on Linux. |
| Cancel during upload | CAS `uploading -> cancelled` | the writer's DB insert finds the state changed and the piece is discarded (410 `gone`) | Files are removed lazily by the sweep once the active count is 0. |
| Accept or complete after cancel or expiry | state check | 410 `gone` | The client shows "cancelled". |
| Double tap or out-of-order accept, decline, complete | CAS affected 0 rows | 200 if already in the target state, else 409 `wrong_state` | The client re-reads the inbox. |
| **[CHG-h]** Revoked device calls | device check | 403 `device_revoked` | The client clears local keys and offers to re-enrol. |
| **[CHG-b]** Pending device never approved | `approved_at IS NULL` and age over 15 min | 403 `not_approved` on any device-scoped call; the sweep deletes it | The user starts registration again. |
| **[CHG-b]** Forged or invalid identity or device signature | server and client verification | 400 `bad_signature` | None. The record is rejected. |
| **[CHG-b]** Identity changed for a pinned person (relay reset, hacked server) | client pin mismatch | Blocking alert "Casey's identity changed"; sends to that person are blocked until the user re-verifies the safety number | Compare safety numbers in person or over a trusted channel. |
| **[CHG-b]** New or changed device for a pinned person | client compares the directory to its pinned device set | Banner and a confirmation required before sending to the new device (decision 18) | The sender confirms or skips that device. |
| **[CHG-g]** Cross-site, wrong origin, missing header, or service-key caller | guard dependency | 403 `bad_origin` or 403 `not_a_person` | Not a client scenario. A browser at another origin never gets this far. |
| DB locked or corrupt | busy timeout, `sqlite3.Error` | 503 `relay_unavailable`; the library path is unaffected | `relay_problems` in healthz. **[CHG-i]** There is no backup: recovery is re-enrolment of all devices. |
| Crash between fallocate and the create commit | dir exists with no row | none | The sweep removes orphan dirs older than 1 h. |
| Row says `ready` but the file is missing | sweep check | error log | Mark the transfer `deleted` and log it. |
| **[CHG-i]** Device offline past prekey rotation | prekey age over 45 days in the directory | `stale: true` in the directory; senders show a warning | The device rotates on next open. Wraps to a stale-but-live prekey are still accepted. |

Error body: `{"error": "<plain words>", "code": "<stable_code>"}`. This keeps the existing `error` key (`app.py:228`).

## 4. Authorization
- **Principal:** an SSO person from `X-Auth-Email`, mapped by `_person`, with `X-Auth-Level: full` (read and write alike on relay routes). Device-scoped calls also send `X-Device-Id`. The admin is a person id listed in `INTAKE_RELAY_ADMINS` (default: Jason's person id).
- **[CHG-g] Request guard, a router dependency on every `/relay` and `/devices` route:**
  - `Sec-Fetch-Site` must be exactly `same-origin` for API calls. A missing header is refused (fails closed). `none` is allowed only for the `/e2e/*` page loads. If `Origin` is present it must equal `https://e2e.home.example.net`.
  - A custom header `X-Novashare: 1` is required on all API calls.
  - `X-Auth-Level` must be `full` even for GET. The gatekeeper alone lets a request with only a drop key and no level through on reads (`app.py:249-258`), so the relay guard refuses it.
  - There is no service-peer, drop-key-only or `/intake`-token exemption for relay routes. `INTAKE_SERVICE_PEERS` and the n8n token door never apply.
  - Relay routes are not reachable from the old drop host (404 at Caddy).
- **Device check:** `device.person == session person AND approved_at IS NOT NULL AND revoked_at IS NULL`, on every device-scoped call. The device id is a bearer value held in IndexedDB; a custom header forces a preflight. Registration is covered by the approval chain below, not by proof of possession (decision 5).
- **[CHG-b] Trust chain:** the person's Ed25519 identity key certifies each device record. Clients pin the identity key (TOFU) and the set of devices. Someone with only an SSO session cannot get a device approved.
  - **First device bootstrap:** if the person has no identity row and no devices, `POST /devices` with `identity_pub` and a self-signed record creates the identity and the device in one insert-once transaction. Anyone who controls an SSO session for a never-enrolled person could do this first, which is why clients alert on pin mismatch and why the admin can reset (below).
  - **New device:** `POST /devices` creates a pending device. An existing approved, live device of the same person approves it with `POST /devices/{id}/approve`, which carries the identity signature over the new record and an encrypted identity-key bundle for the new device (wrapped to its prekey with the same hybrid, so the identity key reaches it only through the approved device). The user confirms a 6-digit code or QR (device id plus fingerprint) on both screens, so the approver knows which device it is approving. Decision 16: the identity private key is on every approved device (non-extractable once imported), so any of them can approve the next.
  - **Admin reset:** if all devices are lost, the admin deletes the identity row (`POST /relay/admin/identity/{person}/reset`). The next first device creates a new identity, and everyone's pin check blocks and asks them to re-verify.
- **[CHG-h] Removal:**
  - `DELETE /devices/{id}` is allowed only from a different approved live device of the same person (`X-Device-Id != id`) or by the admin route. A session with no device, or a device removing itself, is refused (403 `needs_other_device`). A person's last device can only be removed by the admin.
  - Revoke sets `revoked_at`, deletes the push subscription in the same transaction, marks that device's pending and accepted deliveries as declined, and runs the deletion check for the affected transfers.
  - The admin route is `POST /relay/admin/devices/{id}/revoke`, with the same effects (403 `not_admin` otherwise). Revocations only reduce trust, so a server-side revoke is safe to accept without a signature.
- **Scope:**
  - Own devices: list, approve pending, rotate prekeys, subscribe push.
  - Any person's approved live devices' public records via the directory (household members only).
  - Transfers: the sender person creates, uploads pieces and cancels (only the sender device's owner). A recipient device reads only its own wrap, envelope and manifest, and reads the blob only when its delivery is `accepted`.
  - Another person's transfer id gets 404 `not_found`, never 403, so existence is not confirmed (same idea as `resumable.py:173`).
  - The sender can also target their own other devices ("my devices"). The sender device itself is excluded.
- **Enforcement point:** an app guard, a FastAPI dependency `relay_ctx()` that runs the request guard, resolves person and optional device, and is the only way handlers get identity. There is no RLS in SQLite, so every query includes the person or device predicate. A test iterates all routes with the wrong person.
- **Quotas, all in `BEGIN IMMEDIATE` at create:** per-person open bytes (default 2 x MAX_MB, the `OPEN_CAP` idea at `resumable.py:66`), 20 open transfers per person, 10 live devices per person, relay total cap `INTAKE_RELAY_CAP_MB` (default 50 GB). Rate limits in an in-memory token bucket: 10 creates per minute and 5 registrations per hour per person.
- **Push endpoint:** https only, host suffix in an allowlist (`INTAKE_PUSH_HOSTS`: fcm.googleapis.com, updates.push.services.mozilla.com, push.apple.com, notify.windows.com), and DNS must resolve to a public address. This blocks SSRF into the LAN.

## API surface
Common: JSON bodies, binary fields base64url. `(D)` means `X-Device-Id` is required. Every call needs `X-Novashare: 1` and `Sec-Fetch-Site: same-origin`.

**Devices ([CHG-b], [CHG-i]; the paths moved from `/relay/devices`):**

| Method and path | Body / params | Success | Errors (code) |
|---|---|---|---|
| `POST /devices` | `{label, x25519_pub, sign_pub, prekey:{id, xwing_pub, sig}}`, plus for the first device `{identity_pub, record_sig}` | 201 `{device_id, fingerprint, state:"approved"}` for a bootstrap; 201 `{device_id, fingerprint, state:"pending", expires_at}` otherwise; 200 with the existing device if (person, fingerprint) exists | 400 `bad_key`, 400 `bad_signature`, 409 `identity_conflict`, 409 `device_limit`, 429 `rate_limited` |
| `GET /devices` (D) | none | 200 own devices with state (pending, approved, revoked), `last_seen` | |
| `POST /devices/{id}/approve` (D) | `{record_sig, identity_bundle}` | 200 `{state:"approved"}`. The caller must be another approved live device of the same person and the target must be pending. | 400 `bad_signature`, 403 `not_approved`, 404 `not_found`, 409 `wrong_state` |
| `GET /devices/{id}/bundle` (D, the pending device) | none | 200 `{record_sig, identity_pub, identity_bundle}` once approved. The bundle is deleted on first read. | 409 `wrong_state`, 410 `gone` |
| `POST /devices/{id}/prekeys` (D, that device) | `{id, xwing_pub, sig}` | 201. The previous live prekey is retired. | 400 `bad_signature`, 409 `wrong_state`, 429 |
| `DELETE /devices/{id}` (D) | none | 204 (see removal rules above) | 403 `needs_other_device`, 404 `not_found` |
| `GET /devices/directory?persons=a,b` (D) | none | 200 `{persons:[{id, name, identity_pub, devices:[{id, label, x25519_pub, sign_pub, record_sig, created, prekey:{id, xwing_pub, sig, created}, stale}]}]}`. Approved live devices only. `no_devices: true` when empty. | 400 `bad_request` |

**Admin:** `POST /relay/admin/devices/{id}/revoke` (204) and `POST /relay/admin/identity/{person}/reset` (204). Both give 403 `not_admin` to anyone else.

**Relay:**

| Method and path | Body / params | Success | Errors (code) |
|---|---|---|---|
| `GET /relay/push/key` | none | 200 `{vapid_public}` | 503 `push_unavailable` |
| `PUT /relay/push` (D) | `{endpoint, keys:{p256dh, auth}}` | 204 | 400 `bad_push_endpoint` |
| `DELETE /relay/push` (D) | none | 204 | |
| `POST /relay/transfers` (D) | `{start_key, alg:"novashare-v1", targets:{persons:[], own_devices:[]}, size_ct, piece_size, pieces, manifest, manifest_sig, wraps:[{device_id, prekey_id, kem_ct, wrapped_key}]}` | 201 `{id, pieces, expires_at}`; 200 `{id, resumed:true, missing:[...]}` for the same start_key and body hash | 400 `bad_request`, 400 `bad_signature`, 409 `start_key_reuse`, 409 `devices_changed`, 409 `prekey_changed`, 413 `too_big`, 422 `no_devices`, 429 quota or rate codes, 507 `no_space` or `relay_full` |
| `GET /relay/transfers/{id}` (D) | none | Sender: `{state, missing, deliveries:[{device_label, state}]}`. Recipient device: `{state, delivery}`. | 404 `not_found` |
| `PUT /relay/transfers/{id}/pieces/{n}` (D) | raw ciphertext, `X-Piece-Sha256` | 200 `{piece, received, of, already?, state}`; the last piece returns `state:"ready"` | 400 `piece_short`, 409 `piece_conflict`, 409 `not_uploading`, 410 `gone`, 413 `piece_too_long`, 416 `bad_piece`, 422 `checksum_mismatch`, 428 `checksum_required`, 507 `no_space` |
| `POST /relay/transfers/{id}/cancel` (D) | none | 200 `{state:"cancelled"}`; repeat is also 200 | 404, 409 `wrong_state` |
| `GET /relay/inbox?before=&limit=` (D) | limit up to 50 | 200 `{items:[{transfer_id, sender:{person,name,device_id}, size_ct, created, expires_at, state, delivery}], missed:[{transfer_id, sender_name, created, size_ct, reason}]}` | |
| `GET /relay/transfers/{id}/envelope` (D) | none | 200 `{alg, sender_device:{id, person, x25519_pub, sign_pub, record_sig, label, created}, sender_identity_pub, wrap:{prekey_id, kem_ct, wrapped_key}, recipients:[{device_id, prekey_id}], manifest, manifest_sig, size_ct, piece_size, pieces}`. Allowed while the delivery is `pending` or `accepted`. | 404, 410 `gone` |
| `POST /relay/transfers/{id}/accept` (D) | none | 200 `{state:"accepted"}`; idempotent | 409 `wrong_state`, 410 `gone` |
| `POST /relay/transfers/{id}/decline` (D) | none | 200 `{state:"declined"}`; from `pending` or `accepted` | 409 `wrong_state` |
| `POST /relay/transfers/{id}/complete` (D) | none | 200 `{state:"downloaded"}`; only from `accepted`. The client calls it only after the temp file passed every check and was finalized ([CHG-f]). | 409 `wrong_state` |
| `GET /relay/transfers/{id}/blob` (D) | `Range: bytes=a-b` | 200 or 206, `Accept-Ranges`, `Cache-Control: no-store`; requires delivery `accepted` | 409 `not_accepted`, 410 `gone`, 416 `range_not_satisfiable` |

Notes:
- **Why `complete` is explicit:** the server cannot verify the client's decrypt.
- **Sender does not download the blob:** stricter than the brief's "sender or recipient".
- **Blob streaming:** a custom streaming reader using `os.pread` in 1 MiB chunks, not `FileResponse`, so the reader can register and deregister in the active count in a `finally`.
- **[CHG-e] No plaintext fallback:** there is no route on `/relay` or `/devices` that accepts a name, a plaintext file or a plaintext caption. The e2e client has no code path that uploads plaintext. A failed E2E send never retries as a library send.

## State machines
- **Transfer:** `uploading --(all pieces)--> ready`.
  - `uploading|ready --cancel--> cancelled`.
  - `uploading|ready --expiry--> expired`, where expiry means `now >= expires_at` (+7 days), or an uploading transfer with `last_activity` over 48 h old.
  - `ready --(no delivery is pending or accepted)--> deleted`.
  - `cancelled|expired|deleted` are terminal. The sweep deletes the files after the transition.
- **Delivery:** `pending --accept--> accepted --complete--> downloaded`.
  - `pending|accepted --decline--> declined`.
  - A device revoke maps to declined.
  - The deliveries are not visible to the inbox until the transfer is `ready`.
- **[CHG-b] Device:** `pending --approve--> approved --revoke--> revoked`. `pending --(15 min)--> deleted` by the sweep. There is no way back from revoked.
- **[CHG-i] Prekey:** `live --rotate--> retired`. A retired prekey receives no new wraps. Its server row is kept while any wrap references it.

## The sweep (every 10 minutes, `relay_sweep_loop`, started and cancelled in `_lifespan`)
1. **Expire:** `SELECT id FROM transfer WHERE state IN ('uploading','ready') AND expires_at < now`, plus uploading with `last_activity` over 48 h. For each: take the per-transfer guard without waiting, skip if busy, CAS the state to `expired`, then delete the dir only if no reader or writer is active. Otherwise the next run deletes it.
2. **Lazy delete:** for `cancelled|expired|deleted` transfers whose dir still exists, delete it if `_ACTIVE[id]==0`.
3. **Tombstone purge:** delete terminal transfer rows older than 30 days (cascades wrap, delivery, piece).
4. **Orphans:** dirs under `STAGE/relay/` with no row, or a terminal row, older than 1 h are removed. `ready` rows with no file are marked `deleted` and logged at ERROR.
5. **[CHG-b] Pending devices:** delete unapproved devices older than 15 minutes.
6. **[CHG-i] Prekeys:** delete retired prekey rows older than 30 days that no wrap references.
7. **Push:** re-offer deliveries whose claim is older than 5 minutes, up to 5 attempts.
8. **Housekeeping:** trim `audit` at 90 days, a WAL checkpoint (TRUNCATE) once a day, update `sweep_last_run_ts` for the healthz heartbeat.

## Push sender [CHG-e]
- pywebpush with VAPID. `NOVASHARE_VAPID_PRIVATE` (from env, stored in Proton Pass), `NOVASHARE_VAPID_PUBLIC` and `NOVASHARE_VAPID_SUB` (a mailto: contact) come from env.
- A background asyncio task wakes on an event set when a transfer turns ready, and also polls every 5 s. It sends in `asyncio.to_thread` with a semaphore of 4 and a 10 s timeout.
- **The payload is opaque:** `{"v":1,"t":"<transfer_id>"}`. It has no sender name, no size and no names. (This replaces the earlier payload with sender name and size.) The "new device added" security push is `{"v":1,"k":"dev"}`.
- Push TTL is 24 hours, urgency normal. A 404 or 410 deletes the subscription. 429 and 5xx back off.
- **The device builds the notification text:** the service worker (`sw-e2e.js`) fetches the envelope, verifies the chain and `manifest_sig`, decrypts the manifest with the right prekey secret from IndexedDB, and shows for example "Casey wants to send you 3 photos (12 MB)". If anything fails it shows the generic "New Novashare item". The Accept action posts accept and opens `/e2e/inbox?t=<id>`, which starts the download. The Decline action posts decline silently.
- A claim marks `push_claimed_at`, and `pushed_at` is set on success. At-least-once, and the inbox is the source of truth.
- **[CHG-h]** Revoke deletes the subscription row in the same transaction as the revoke, so a revoked device cannot get any further payload.

## Client module boundaries (responsibilities only)
- **`keys.js`**
  - Generates device keys: static X25519 and Ed25519 signing (WebCrypto non-extractable where the browser has them, otherwise the vendored implementation), and X-Wing prekeys from `mlkem768.min.js`.
  - Owns the IndexedDB schema: identity key (non-extractable once imported), device record, prekey secrets under a non-extractable AES-GCM wrapping key, retired prekey secrets with their deletion time, pinned identity and device sets per person, verified-safety-number flags, and the persisted content key, nonce prefix and `start_key` for in-flight sends.
  - **[CHG-b]** Bootstrap and approval flows: creates the identity key on the first device, shows the pending QR and 6-digit code, scans or types the code on the approver, signs the new record, wraps the identity bundle to the new device, and imports it.
  - Implements the wrap and unwrap KDF, the chain verification (pinned identity to device record to prekey), pin checks and alerts, and safety numbers.
  - **[CHG-i]** Rotates the prekey every 30 days on app open or from the service worker's periodic wake. The retired secret is deleted 8 days after retirement (7-day transfer life plus a day of margin), and a test with a faked clock checks this.
  - Safety numbers are per person pair, derived from the two identity public keys, so they stay stable when devices change (this replaces the per-device-pair number).
- **`relay.js`**
  - API client: adds `X-Device-Id` and `X-Novashare`, maps stable error codes to plain-language messages, handles `devices_changed` and `prekey_changed` by re-fetching the directory and re-wrapping, and blocks or asks on a new or changed device or an identity mismatch.
  - Content-key generation, piece encrypt and decrypt, manifest encode, sign, verify and decode. The per-file SHA-256 and the total are computed while encrypting.
  - Parallel piece upload with resume via `GET /relay/transfers/{id}`, reusing the pattern and the per-piece `crypto.subtle.digest` helper of `atomic.js` (`atomic_ui.py:126`).
  - **[CHG-f]** Range download with piece-aligned decrypt into a temp file (OPFS or the best the browser has; the Android Chrome streaming-save question stays open in the risks). Finalize (move to the visible name, or offer the save) only after the last piece with the last-flag, the total length, the file count and each per-file SHA-256 all match the signed manifest. On any mismatch the temp file is deleted and `complete` is not called.
- **Inbox UI (`inbox.js`, page `/e2e/inbox`)**
  - Inbox list with pending, accepted and recent items, and the "missed" list. Accept and Decline, progress, and plain error messages.
  - **[CHG-b]** Pending-approval screens, device list with revoke from another device, key-change and new-device banners, safety-number screen.
  - Push enable and disable. All DOM writes go through `textContent` or the one Trusted Types policy. There is no inline script or inline handler anywhere.
- **`sw-e2e.js`:** push handler and notification actions as in the push section, plus its own caching rules. It does not touch the old origin's `sw.js`.

## 6. Observability
- **Logs:** one line per state change through `app.say` (`app.py:284`), for example `relay create t=ab12cd34 pieces=40 devs=3 rid=9f3a`. Fields are an 8-character transfer id prefix, a 6-character device id prefix, sizes, counts, the code and a request id. There are no names, emails or person ids. Levels: INFO for transitions, WARNING for 4xx security refusals (`bad_origin`, wrong device, `bad_signature`, `needs_other_device`), ERROR for DB, disk or push failures.
- **Metrics (the lightest fit):** state lives in SQL, not in process memory.
  - `counter` rows bumped in the same transaction as each transition: `transfers_created`, `ready`, `cancelled`, `expired`, `accepted`, `declined`, `downloaded`, `push_ok`, `push_gone`, `push_fail`, `piece_checksum_fail`, `quota_refused`, `device_approved`, `device_revoked`, `guard_refused`.
  - Gauges computed from SQL: open transfers, `bytes_held`, `oldest_pending_age_s`, devices live, `sweep_last_run_ts`.
  - A small `relay_stats.py` run from NUC cron every minute writes `intake_relay.prom` atomically to the node_exporter textfile dir with `intake_relay_*` names. This needs the textfile collector to exist, which I did not verify (decision 10). If it does not, the same numbers go as JSON in `/healthz`. The textfile script reads `relay.db` read-only.
- **Healthcheck:** add a `relay` block to `/healthz` (`app.py:469`). It checks a `BEGIN IMMEDIATE; ROLLBACK` within 1 s, the sweep heartbeat age under 30 min, VAPID env present (presence only, no send), and relay free space (via the shared disk reserve). Its failures go in `relay_problems`, not `problems`, so they never turn the response into 503 for the library path (decision 8). The e2e host does not expose `/healthz`.
- **Trace propagation:** inbound is `X-Request-Id` if present, else generated, and it is only logged. Outbound push gets no trace header. The id appears in the push attempt log line.

## What must NOT change for the existing library path
- No edits to `resumable.py`, `share.py` logic, `classify.py`, `scan.py`, `place()`, `route_file`, or the gatekeeper's rules for `/intake`, `/upload`, `/share`, `/up/*`.
- `/up/*` request and response shapes, the 428 checksum rule, `.got` and `.json` meta files, and the finish and lock behaviour stay as they are. Relay pieces never write into the `STAGE/*.part` namespace.
- `app.py` gets only these additions: include the relay and devices routers, start and cancel two tasks in `_lifespan` next to `resumable.sweep_loop` (`app.py:108`), and the `relay` block in healthz.
- **[CHG-a]** The old drop host's Caddy block, pages, `sw.js` (`share.py:138`) and `atomic.js` are not edited by this work (step 5 adds a link to the new app, nothing else). The old host does not proxy `/relay/*` or `/devices/*`. The new e2e site block is the only Caddy addition.
- `relay` imports from `resumable` only the constants `STAGE` and `RESERVE`, read-only.
- A relay outage never changes the `/healthz` status code.
- The relay test suite must include the full existing regression (`~/sandbox/intake_regress_live.sh`, printing `REGRESSION: ALL PASS`) after each swap, using the swap procedure in the review README.

## Build sequence (plan steps 1-6; step 0 is this document)
1. **Step 1, origin, identity and device registry.**
   - Build:
     - The e2e Caddy site block: forward_auth like the drop site, path allowlist, CSP and the other headers, `max_size`.
     - Verify the SSO cookie works on the e2e host (decision 15).
     - Exclude `STAGE/relay.db*` and `STAGE/relay/` from backups, and record where that was checked.
     - `relaydb.py` (open in lifespan, step-1 migration: person_identity, device, device_prekey, audit, counter).
     - The `relay_ctx` guard.
     - `/devices` routes: register, approve, bundle, prekeys, delete, directory, admin revoke and reset.
     - `keys.js` with identity, approval and pinning, and a minimal devices page.
   - Tests:
     - Playwright on the e2e origin: an inline script is blocked, a Trusted Types violation is reported, and a tampered `mlkem768.min.js` fails SRI and does not load.
     - The same paths on the old host give 404.
     - Guard matrix: `Sec-Fetch-Site` of cross-site, same-site, none and missing, a missing `X-Novashare`, and a request with only the drop key all give 403.
     - Schema triggers: keys and `record_sig` immutable, revoke is one-way, one live prekey.
     - First-device bootstrap, and two simultaneous bootstraps (one wins, one becomes pending).
     - Pending device approved by another device with a good signature; a forged or wrong-person signature gives 400 and 403; a pending device expires at 15 min.
     - Remove from the same device or from a session with no device gives 403; admin revoke works; revoke deletes the push sub.
     - Wrong person gets 404, view-level gets 403, an unmapped email gets 403.
     - The client raises a banner on a new device and a blocking alert on an identity change.
     - Importing `app` in a second process never creates `relay.db`.
2. **Step 2, encrypted send.**
   - Build: migration 2 (transfer, recipient, wrap, delivery, piece), the create and piece routes, the fallocate path, the quota transaction, server-side signature checks, and `relay.js` send with X-Wing wraps, the sender-static term and the signed manifest.
   - Tests:
     - Create replay with the same key returns the same transfer.
     - `devices_changed`, `prekey_changed` and `no_devices` paths.
     - A bad `manifest_sig` gives 400.
     - Piece idempotence, checksum failure, short piece, `piece_conflict`.
     - Kill and restart the server mid-upload, then resume.
     - A 2 GB send in a sandbox.
     - Canary grep over STAGE, `relay.db`, `relay.db-wal` and the logs finds no plaintext and no file name.
     - Known-answer tests for the wrap KDF against a second implementation (a Python script using the same vendored vectors).
     - Concurrent creates from one person cannot exceed the quota.
     - Disk-full gives 507 with no orphan row or file.
     - The existing library regression passes.
3. **Step 3, receive.**
   - Build: inbox, envelope, accept, decline, complete, blob with Range, the deletion check, `inbox.js`, streamed decrypt into a temp file with finalize-after-verify.
   - Tests:
     - State machine transitions (every illegal edge gives 409), double tap, accept after cancel gives 410.
     - Range requests at piece boundaries and at 416.
     - Wrong person and wrong device on every route.
     - Pieces only after accept.
     - Deleted when the last delivery completes or declines.
     - A tampered byte, a dropped last piece, a manifest total that disagrees, or a per-file hash mismatch: the temp file is deleted, nothing is finalized and `complete` is not called.
     - A server-swapped manifest or wrap fails signature or AEAD.
     - Two-browser Playwright test round-trips a file byte for byte.
4. **Step 4, push.**
   - Build: migration 4 (push_sub), `push.py` with pywebpush and VAPID, the allowlist and public-IP check, the push loop, `sw-e2e.js` handlers (decrypt and build text), the "new device added" push.
   - Tests:
     - Subscribe with a bad endpoint gives 400.
     - 410 from a stub push server drops the sub.
     - Push down leaves the inbox working.
     - At-least-once claim and retry caps.
     - The payload equals `{"v":1,"t":...}` and nothing else, checked on the wire in the stub.
     - The notification text appears after local decrypt; a failure gives the generic text.
     - Revoke removes the sub.
     - Accept from the notification works on Android.
   - Add `pywebpush` to the pinned requirements and rebuild the image through the existing swap procedure.
5. **Step 5, share target, destination picker and the library hand-off.**
   - Build: a separate PWA on the e2e origin (own manifest, own name, own share target) with the people and own-devices picker. "My library" opens the existing drop page on the old host, labelled "the server can read this" (decision 14). The old-host share flow stays unchanged.
   - Tests: install and share on Android; the existing share-target regression; the black/purple/orange theme; an E2E send never falls back to the library.
6. **Step 6, expiry, cancel, quotas, audit, rotation.**
   - Build: the sweep, cancel, counters, `relay_stats.py`, the healthz block, prekey rotation and retired-secret deletion.
   - Tests:
     - Sweep with a faked clock expires and deletes, and skips a transfer with an active reader.
     - Cancel during an upload.
     - Concurrent sends against the quota hold.
     - The orphan dir and orphan row cases.
     - Pending devices removed after 15 min.
     - Prekey rotation: old prekey refused for new wraps, still decrypts an in-flight transfer, secret deleted at 8 days.
     - Healthz returns 200 with `relay_problems` populated when the relay DB is locked.
     - Metrics file written atomically.
     - Backup config check repeated.

## Risks and open questions
- Android Chrome has no reliable streamed save-to-disk API. Receiving a multi-GB file on a phone may need OPFS temp files plus a share or download handoff. Step 3 needs a spike before committing. This is the largest unknown.
- Native WebCrypto support for X25519 and Ed25519 varies by browser version, and ML-KEM or X-Wing is not native, so the vendored fallback matters. Key and signature formats must match between native and vendored paths, so test both.
- **[CHG-a]** The SSO cookie must be valid on `e2e.home.example.net` (the cookie domain and the forward_auth `/verify` redirect). I did not read the auth service or Caddy files. If the cookie is host-only, the e2e host needs its own login round trip.
- **[CHG-a]** Two installed apps (the existing Novashare and the new e2e one) is a UX cost, and "My library" is a hand-off rather than a destination in the same app. The alternative, proxying `/up/*` on the e2e host and reusing it from the new client, adds scope and puts the library path on the strict origin. I recommend the hand-off for v1.
- **[CHG-a]** The service worker cannot use SRI, so its integrity rests on the content-hash name and the CI check.
- **[CHG-b]** First-device bootstrap is trust on first use. Anyone who controls the SSO session for a never-enrolled person can claim the identity first. The pin alert and the admin reset are the mitigation. Real-world risk is low for a household.
- **[CHG-b]** The identity private key lives on every approved device. A stolen unlocked device can approve an attacker's device. The 6-digit/QR confirmation and the "new device" alert on the other devices are the mitigation.
- **[CHG-i]** With the relay excluded from backups, a failed disk or rebuild means every device must re-enrol and every pin will alert once. That is intended, but say so to the household in advance.
- **[CHG-i]** Bounded forward secrecy covers the KEM prekey only. The static X25519 device key and the identity key are long-lived, so they do not give forward secrecy for the authenticity terms.
- A hacked server can still hand out a fake directory to a first-time sender. TOFU, the identity pin, the new-device alert and safety numbers reduce but do not remove this. The plan accepts it.
- The notification Accept path depends on the SSO session cookie reaching the service-worker fetch. A forward_auth redirect to login makes that fetch fail, and the fallback is opening the page.
- Whether Caddy sets a request id header and whether node_exporter's textfile collector exists are unverified. The design works either way.
- `rescan_held` already imports app hourly. Any future relay code that opens the DB at import time would break the design silently, so keep the process-boundary test in the suite.
- The relay and the libraries share one disk (`STAGE` is on the library disk). The reserve and the relay cap are the only protection, so tune them after the first big send.

## Decisions for the owner to confirm
Kept from the first draft (marked if changed):
1. Nonce prefix per transfer: 8 random bytes once per transfer, plus the piece index, so retries after a reload are byte-identical. The plan reads as random per piece, which would break resume.
2. **[CHG-c]** The sender-static term stays, now inside the X-Wing based wrap KDF together with the full context binding (superseded in form, kept in substance).
3. Fixed 4 MiB plaintext pieces for every size (the existing engine uses 16 MB above 256 MB).
4. One transfer is one concatenated stream with offsets in the signed manifest. **[CHG-e]** The server never learns the file count, and the push payload no longer says anything about the content.
5. Device auth is the SSO session plus the `X-Device-Id` header. **[CHG-b]** The approval chain, not proof of possession, stops a rogue registration.
6. View-level people cannot use the relay, because the gatekeeper requires `full` for writes and the relay guard requires it for reads too. If Casey or Robin are view-level they would need full.
7. **[CHG-e]** Accept from a notification marks accepted and opens the app to download. It does not download inside the service worker.
8. Relay faults go in `relay_problems` and never turn `/healthz` into 503.
9. Logs carry no person ids, only device and transfer prefixes. Existing lines do use person ids (`app.py:788`), so say if you want parity.
10. Metrics use a counter table plus a cron textfile script, with a `/healthz` JSON fallback. This needs the node_exporter textfile collector, which I did not verify.
11. Push endpoint host allowlist (`INTAKE_PUSH_HOSTS`) plus a public-IP check.
12. Default caps: 20 open transfers, 2 x MAX_MB open bytes per person, 10 devices per person, 50 GB relay total.
13. Retention: tombstones 30 days, stale uploads 48 h, audit 90 days, sweep every 10 minutes.
New or changed by your decisions (a)-(i):
14. **[CHG-a]** "My library" is a hand-off link to the old drop host, from a separate e2e PWA, not a destination inside the e2e app. The alternative is proxying `/up/*` on the e2e host.
15. **[CHG-a]** The SSO cookie must work on the e2e host. I could not verify the cookie scope, so this needs a check in step 1.
16. **[CHG-b]** The identity private key is shared to every approved device, so any device can approve the next. The alternative is a per-device certificate chain with the identity key on one device only, which makes losing that device a reset.
17. **[CHG-b]** First-device bootstrap is allowed for any SSO person who has no identity yet, and the admin can reset an identity. Pending devices expire after 15 minutes.
18. **[CHG-b]** A send to a person who has a new or changed device since the sender last pinned requires a confirmation tap (default: confirm required, not silent).
19. **[CHG-c]** The vendored `mlkem768.min.js` must provide X-Wing (ML-KEM-768 plus X25519 with the SHA3-256 combiner). If it only has plain ML-KEM, the combiner is added in the same bundle. The HKDF-SHA256 second stage combines the X-Wing secret with the sender-static secret.
20. **[CHG-c]** Prekey secrets live in IndexedDB under a non-extractable AES-GCM key, not as native non-extractable keys, because X-Wing is not native. The static X25519 and Ed25519 device keys are non-extractable where the browser supports them, with a vendored fallback.
21. **[CHG-d]** A per-device Ed25519 signing key is added to the device record, and the sender device signs a transcript that covers the manifest ciphertext and the recipient list.
22. **[CHG-h]** Removing a device needs another approved device of the same person or the admin (`INTAKE_RELAY_ADMINS`, default Jason's person id). The last device of a person can only be removed by the admin.
23. **[CHG-i]** Prekeys rotate every 30 days; a retired prekey's secret is deleted 8 days after retirement; the directory flags a prekey older than 45 days as stale, but the server still accepts wraps to it.
24. **[CHG-i]** `STAGE/relay.db*` and `STAGE/relay/` are excluded from backups, so the recovery path is re-enrolment of every device. I did not read the backup configuration, so step 1 includes checking it.
25. New dependencies: `pywebpush` pinned in the intake image (and the `cryptography` library it brings, used for server-side signature checks), plus the vendored client bundles with recorded hashes.

Files read: `D:\scratchpad\nuc\intake\NOVASHARE-E2E-PLAN.md`, `app.py`, `resumable.py`, `dropconfig.py`, `review-2026-11-23\README.md`, `review-2026-11-23\races.md`. `atomic_ui.py` and `share.py` were only grepped.