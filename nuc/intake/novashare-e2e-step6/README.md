# Novashare E2E, Step 6: expiry, quotas, sender cancel, audit log (2026-11-24)

Sandbox-tested, **not live**. Plan: `../NOVASHARE-E2E-PLAN.md` step 6. Builds on Steps 1-5 (all live).

## What it adds

- **The sweep**, every 10 minutes, in a thread of the intake app (not a cron: it must see the downloads and uploads
  running in the app right now, and it starts and stops with the app). It:
  - expires sends 7 days after create, and uploads with no piece for 6 hours (their quota is released);
  - finishes a send whose receiving devices were all removed;
  - marks a send whose file is missing as deleted (logged as an error);
  - purges finished rows 24 h after the last change (cancelled ones not before their 7 days, so receivers still see
    "cancelled");
  - removes leftover folders: no row and older than 1 h (a crash during create), or a finished row (a crash between
    the commit and the delete), plus rows with no send. Only `RELAY/<22-char id>/data` and its empty folder are ever
    removed, by a path rebuilt from a checked id. Links are removed, never followed. Nothing else is touched.
  - Safe if two sweeps overlap or the app restarts mid-run: every change is a compare-and-set, every delete can
    repeat, and a lease row stops two sweeps from running at once.
- **Cancel** ("Cancel this send", two taps, on the Send page): the server deletes the key wraps and the scrambled
  pieces. The receivers' inbox shows "Cancelled by the sender". Their devices get "A Novashare item was withdrawn"
  (no names). A download in progress stops at the next piece and removes the unfinished file.
- **Quotas that hold under parallel sends:** per person 2 x the file limit in open sends and 20 open sends, relay
  total 50 GB, and now **20 new sends a minute and 120 an hour per person**. All are checked in one locked database
  transaction, so ten sends at once cannot overrun them.
- **Audit log without content:** `.novashare-audit/audit.jsonl` beside the relay store. A line holds the event, the
  transfer id cut to 8, sizes and counts, the device id hashed (HMAC, per-install key), and the time. It never holds a
  name, email, file name or caption. It rotates at 1 MB (5 old files), and old files are deleted after 90 days.
  Folder 700, files 600. Jason reads it at `/relay/api/audit`; everyone else gets 404.
- **The Relay line** on Jason's Devices page shows the live sends, the bytes held and the last sweep ("LATE" after
  30 min). Others do not see it. `/relay/api/relay-stats` gives the same numbers, for Jason only.

## Files

- `sweep.py`, `audit.py`: new.
- `relay.py`: the rate limit, audit lines, and the in-flight piece count; a piece written after a cancel gets 410.
- `receive.py`: "cancelled" in the inbox, sends that were never ready are hidden, audit lines, reads counted.
- `push.py`: the `withdrawn` kind. Its trigger queues the push in the same transaction as the cancel. A one-time
  rebuild of the outbox table adds the new kind to its CHECK.
- `receive_js.py`: the worker's withdrawn text, "Cancelled by the sender", and the cut-off download message.
- `relay_js.py`: the Cancel button and the cancelled / expired tags.
- `devices.py`: the `RELAY_LINE` hook.
- `novashare_sweep_patch.py`: the patcher. It refuses to run if a live file changed since 2026-11-24 (sha256 check).
- `test_sweep.py`: 82 server checks. `test_sweep_browser.py`: two people in Chrome. `sweep_sbx.sh`: `test | up | relay | log | grep | down`.

## Tests (sandbox, 2026-11-24)

- `sweep_sbx.sh test`: test_sweep.py ALL PASS (82). The regressions also pass: test_pick (102), test_relay (100),
  test_receive (80), test_devices (80), test_push (84), test_abyss_handoff (15) and test_ui. `/sending` and the
  manifest are identical to live. `node --check` passes on inbox.js, relay.js and /relay/sw.js.
  - Concurrency: 10 parallel creates against each limit (bytes, count, relay total, rate). Exactly the allowed
    number pass every time.
  - Sweep with a faked clock: every rule above, including a skip while a piece is being read.
  - Two sweeps at once: each send expires once.
  - Crash after the commit, in-process and as a killed process (`os._exit`): the next run (after the dead lease
    ends) finishes the job.
  - The outbox migration was run on a Step 4 table.
- `test_sweep_browser.py` ALL PASS (Jason's PC + Casey's phone, real Chrome, Caddy sign-in front door):
  - Cancel after ready: Casey's inbox shows "cancelled" and the relay folder is gone.
  - Cancel during Casey's 30 MB download: the next piece gets 410, her card says so, and the partial file is removed.
  - 10 sends of 6 MB started at once against a 40 MB cap: exactly 6 are ready and 4 get quota_bytes.
  - Jason sees the Relay line; Casey does not.
  - No plaintext or file name is on the relay. No CSP errors.
  - The sandbox was torn down after.
- Not tested: a real phone getting the "withdrawn" push. The payload and the queueing are unit-tested; the live
  check is after the swap.

## Swap (when Jason says so)

Only `*.py` files change (no package, no compose change):

```
bash ~/sandbox/horizon/SWAP-PATCH.sh novashare-sweep ~/sandbox/novashare-sweep/novashare_sweep_patch.py ~/sandbox/novashare-sweep/test_sweep.py
```

After the swap, check: the intake log shows `relay sweep on`, then about a minute later a `relay sweep ...` line.
Jason's Devices page shows the Relay line. Then send yourself something and cancel it from the Send page.
