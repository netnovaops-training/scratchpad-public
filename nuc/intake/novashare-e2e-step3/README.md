# Novashare E2E, Step 3: receive (2026-11-24)

Sandbox-tested, **not live**. Plan: `../NOVASHARE-E2E-PLAN.md` step 3. Builds on Step 1 (devices) and Step 2 (send),
both live.

## What it adds

- **Inbox page** `/relay/inbox`: what was sent to *this* device. The server lists sender, size and time only. The
  device unlocks its own key wrap and shows the file name and note after it decrypts them locally.
- **Accept**: the browser fetches the pieces one by one (3 ahead), checks each piece's length, decrypts it in
  order with its own nonce and AAD (piece number plus a "last piece" flag), and writes it straight to disk. The file
  is finalized only after the last piece and the total size check out. Then the device tells the server "received".
  Any failed check stops the save and removes the unfinished file. The server is not told "received", and Accept
  can be tried again.
- **Decline** (press twice): deletes this device's key wrap and tells the sender.
- **Sender status**: the Send page has a "Sent" list that shows, for each receiving device, whether it is waiting,
  accepted but not saved, saved or declined.
- When no device is still waiting or saving, the transfer is marked finished and its ciphertext is deleted.

## Where files are saved (feature-detected)

| Browser | How | Notes |
|---|---|---|
| Chrome / Edge on the PC | **A folder you pick once**, for example `D:\Novashare\Inbox` | Remembered, so later saves go there without asking. Chrome writes to a hidden swap file and turns it into the real file only when it closes it. |
| Android Chrome | **Download**: a service worker streams the decrypted file into Downloads | If a piece fails its check, the download is marked failed. |
| Others with a save dialog | Asks per file | |
| Anything else | In memory, up to 256 MB | Last resort. |

The whole file is never held in memory, except in the last-resort mode. At most 3 fetched pieces (4 MiB each) are,
plus the one being written.

## Malware scanning

The server cannot scan these files. It only ever holds ciphertext and never has the key, so server-side
antivirus is impossible by design. On the PC, **Windows Defender scans each file automatically** when Chrome
finishes writing it into the folder. Chrome also runs its own download check when it closes the swap file. If
`D:\Novashare\Inbox` is the folder, KopiaUI backs it up like the rest of `D:`. On Android, Play Protect does not
scan downloads, so open only files you expected.

## API (all signed in, `X-Novashare: 1`, same-origin, `X-Device-Id` = your own live device)

| Route | What |
|---|---|
| `GET /relay/api/inbox` | Deliveries to this device (no names) |
| `POST /relay/api/transfers/{id}/accept` | pending -> accepted |
| `POST /relay/api/transfers/{id}/decline` | pending or accepted -> declined; this device's wrap is deleted |
| `POST /relay/api/transfers/{id}/complete` | accepted -> downloaded; the wrap is deleted (no replay) |
| `GET /relay/api/transfers/{id}/pieces/{n}` | Ciphertext piece, only while this device's delivery is accepted |
| `GET /relay/api/sent` | Your sends, each with every receiving device's state |

If someone else's device asks for a transfer, it gets the same 404 as for a transfer that does not exist. The
sending device cannot download its own pieces.

## Files

- `receive.py`, `receive_js.py`: new (routes; page script, style, service worker).
- `relay.py`: the `delivery` table, its triggers and a one-time backfill in `_SCHEMA`, plus the Sent list and
  inbox link on the send page.
- `relay_js.py`: the Sent list.
- `novashare_receive_patch.py`: the patcher for `SWAP-PATCH.sh`.
- `test_receive.py`: server rules, run inside the patched image.
- `test_receive_browser.py`: two Chrome profiles (Jason and Casey) through a Caddy front door on the NUC sandbox.
- `receive_sbx.sh`: sandbox `test | up | tamper | state | grep | down`.

## Tests (sandbox, 2026-11-24)

- `receive_sbx.sh test`: `test_receive.py` ALL PASS, plus Step 2's `test_relay.py` and Step 1's `test_devices.py`
  as regressions.
- `test_receive_browser.py` (ALL PASS, 34 checks): 50 MB through the download path (2 s) and 2 GB through the
  folder path (40 s, 51 MB/s), both byte for byte. The largest Chrome process peaked at 399 MB during the 2 GB
  receive. The 2 GB send from Chrome took 89 s.
  A tampered piece is refused in both paths and nothing is left behind. The decline flow shows on the sender's page.
  Replay of a used wrap gets 404. The grep for plaintext, names and the note finds nothing. No CSP errors.

## Not yet (later steps)

Push notifications (4), the share sheet (5), expiry, the sweep and cancel (6). Until step 6, ciphertext left by a
failed `rm` after "finished" stays until the sweep exists. `complete` is trusted from the device, because the server
cannot check a decrypt.

## Swap (when Jason says so)

```
bash ~/sandbox/horizon/SWAP-PATCH.sh novashare-receive ~/sandbox/novashare-receive/novashare_receive_patch.py ~/sandbox/novashare-receive/test_receive.py
```
