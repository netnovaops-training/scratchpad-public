# Novashare E2E, Step 4: web push notifications (2026-11-24)

Sandbox-tested, **not live**. Plan: `../NOVASHARE-E2E-PLAN.md` step 4. Builds on Steps 1-3 (devices, send, receive),
all live.

## What it adds

- **"Notify me on this device"** on the Devices page and the Inbox page. The browser asks for permission only after
  that tap. The subscription (push address + its two keys) is stored for **this device only**.
- **A new send for you**: every subscribed device of yours that the send was locked for gets
  "You have a new Novashare item". **Accept** opens `/relay/inbox#<id>`. The page scrolls to that item and focuses
  "Accept and save". One tap saves it: the folder permission, the file picker and downloads all need a tap. The file
  is unlocked and decrypted in the page, as in Step 3.
- **Your send was saved or declined**: your own subscribed devices get "Your Novashare item was delivered"
  (or "...was declined"). A tap opens the Send page.
- **Send a test** button (5 per 10 minutes) and **Turn off**.

## What a push carries

Only `{"v":1,"kind":"new","id":"<transfer id>","body":"You have a new Novashare item"}` (for "delivered", also
`"result":"saved"` or `"declined"`). It carries **no names, captions, file names or sizes**. It is encrypted to the
browser (RFC 8291, aes128gcm), so the push service (Google, Mozilla, Apple, Microsoft) cannot read even that. The
service worker shows its own fixed text for each kind, and it accepts only an id with the shape of a transfer id.

## iPhone and iPad

Web push works **only for a Home Screen web app, on iOS / iPadOS 16.4 or later**. A Safari tab has no push. On an
iPhone in Safari, the Notifications box explains how to do it: tap Share, then "Add to Home Screen", then open
Novashare from that icon and tap "Notify me" there. Android Chrome, desktop Chrome, Edge and Firefox work in a normal tab.

## How it works

- `push.py`: the routes, the `push_sub` table (one row per device, endpoint unique) and the `push_out` outbox, filled
  by SQLite triggers **in the same transaction** as the event: a transfer turning `ready`, or a delivery turning
  `downloaded` / `declined`. `relay.py` and `receive.py` code is unchanged. One worker thread, started with the app,
  drains the outbox, so no request waits for a push service.
- Push service answers: 2xx = sent. 404/410 = the subscription is deleted. 429, 5xx, a timeout (10 s) or a redirect =
  **one** retry after 30 s (or Retry-After, up to 10 min), then it is dropped. Other 4xx = dropped and logged.
  A push still queued after 1 day is dropped.
- **Revoke** a device: a trigger deletes its subscription and its queued pushes in the same transaction.
- Endpoints must be https on a known push service (`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
  `*.push.apple.com`, `*.notify.windows.com`), with no redirects followed. The server POSTs there, so anything else
  would be an SSRF door. `INTAKE_PUSH_TEST_ORIGINS` adds exact origins for the sandbox only, and it is logged.
- Log lines: kind, transfer id (8 characters), device id (6 characters), push service **host** and status. The
  endpoint path is a capability URL and is never logged.

## VAPID key

Made once by `make_vapid_key.sh` on 2026-11-24. The private key went through stdin straight into Proton Pass, in the
"NUC services" vault, as the item `NUC VAPID_PRIVATE_KEY novashare push (2026-11-24)` (field `password`). The script
read it back, compared it and printed only its length and the public-key fingerprint `f25cfa73`. The key is never
printed or logged. Intake derives the public key from it and serves it at `/relay/api/push/key`. If the key changes,
old subscriptions are dropped and the page offers "Notify me" again. If there is no key, push is off and nothing
else changes.

Line for `~/nuc-homelab/secrets.refs` (Jason adds it; `push_swap.sh` stops without it):

```
VAPID_PRIVATE_KEY=pass://NUC services/NUC VAPID_PRIVATE_KEY novashare push (2026-11-24)/password
```

## Packages (pinned, all at least 2 weeks old on 2026-11-24, pip-audit clean)

pywebpush 2.5.0, py-vapid 1.9.6, http-ece 1.2.1, cryptography 50.0.2, requests 2.34.2, urllib3 2.8.0, aiohttp 3.14.5
and their dependencies (`novashare_push_patch.py` has the list). charset-normalizer is held at 3.5.2, because 3.5.3 was
one day old.

## Files

- `push.py`, `push_js.py`: new (routes, outbox, worker; the client script).
- `devices.py`, `receive.py`: the Notifications box in the Devices and Inbox pages. Routes are unchanged.
- `receive_js.py`: the service worker gains `push` and `notificationclick`; the inbox focuses `#<id>` and refreshes
  when a push arrives.
- `novashare_push_patch.py`: the patcher. It copies the files above, adds the router and the worker to the end of
  `app.py`, and appends the packages to `requirements.txt`.
- `test_push.py`: the server rules (84 checks), run inside the patched image, including one real pywebpush send
  to an in-process mock push service.
- `test_push_e2e.py` + `mock_push.py`: the sandbox end-to-end.
- `push_sbx.sh`: `test | e2e | down`. `make_vapid_key.sh`: the one-time key. `push_swap.sh`: the swap.

## Tests (sandbox, 2026-11-24)

- `push_sbx.sh test`: `test_push.py` ALL PASS (84), plus `test_receive.py` (80), `test_relay.py` (100) and
  `test_devices.py` (80) as regressions. pip-audit found no known vulnerabilities.
  Covered: the guard on every route; only your own live device can subscribe, read state, unsubscribe or test; 14
  hostile endpoints refused; key shapes; one row per device; another person's endpoint gets 409; a push only once the
  send is ready and only to subscribed pending devices; no names in the payload; delivered pushes; 410 and 404
  cleanup; 5xx and timeout retry once; a redirect is not followed; revoke and unsubscribe; a key change; push off; the
  test rate limit; page SRI; the worker handlers; a real aes128gcm decrypt plus VAPID JWT verification; the worker
  thread; a clean log.
- `push_sbx.sh e2e`: real uvicorn (the lifespan starts the worker) and a mock push service in separate containers on
  an internal docker network. ALL PASS. The captured push for Z1 was decrypted with Z1's private key and auth secret
  and was exactly `{v, kind:new, id, body}`. The VAPID ES256 JWT verified against the served key. The upload
  answered in under 2 s while the push went out separately. 410 deleted the subscription. The flaky endpoint got 503,
  then one retry, then delivery. Save on Z1 sent a "delivered" push to Jason's J1. Revoke removed the subscription.
  The server log had no names, emails, labels, endpoint paths or key.
- JS syntax: `node --check` on push.js, inbox.js and sw.js.
- Not tested here: a real phone. That is the live check after the swap.

## Swap (when Jason says so)

First add the secrets.refs line above. Then:

```
bash ~/sandbox/novashare-push/push_swap.sh
```

It runs `SWAP-PATCH.sh novashare-push ...`, which tests, backs up, installs the code and rebuilds; push is off at
that point and nothing breaks. Then it installs `requirements.txt`, adds `- VAPID_PRIVATE_KEY=${VAPID_PRIVATE_KEY:-}`
to the intake service in `docker-compose.yml` (backups of both in `~/archive/<date>/novashare-push/`), rebuilds through
`~/bin/dc`, checks health, checks for "push on", and runs the live regression. The undo line is printed at the end.

Phone check after the swap: open Novashare on the phone (on iPhone, from the Home Screen icon), go to Inbox, tap
"Notify me on this device", then "Send a test". Then send yourself something from the PC and tap **Accept** on the
notification.
