# Novashare: person-to-person sharing, end-to-end and post-quantum (plan, 2026-11-23)

Jason, 11-23: "Think of this feature like Samsung Drop, Samsung Share, or Amazon Drop, without all the hardware
implications that it has and post-encryption." His answers the same evening:

- **Encryption:** end-to-end with a post-quantum key exchange.
- **Recipients:** people and their devices.
- **Receiving:** push notification with Accept and Decline.
- **Libraries:** keep "file into my library" as one destination.

## What it is

You share from any app's share sheet, or from the Novashare page, and pick a destination:

- **A person** (Casey, Robin, Mom...): the item arrives on every device that person has signed in.
- **One of your own devices:** phone, PC or tablet.
- **My library:** today's filing into Paperless, Kavita or Jellyfin.

The receiver gets a push notification, for example "Casey wants to send you 3 photos (12 MB)", with Accept and
Decline. Accept downloads and decrypts on that device. Declined or ignored items are deleted after 7 days.

None of the hardware parts are used (no Bluetooth, Wi-Fi Direct or UWB). The NUC is the relay. Phones reach it
over the internet, so this works anywhere, not only nearby.

**The NUC never sees a file or its name.** The sender's browser encrypts everything before it leaves the phone.
The NUC stores scrambled pieces, plus who sent to whom, the size and the time. Only the receiver's devices hold the
keys. Library filing is the one exception, and the app says so: the server must read a file to sort it.

## Crypto design (to be checked in Step 0 and audited in Step 7)

| Part | Choice | Why |
|---|---|---|
| Device keys | Each device makes two key pairs on first use: X25519 (WebCrypto, private key non-extractable) and ML-KEM-768 (FIPS 203). | Hybrid: an attacker must break both. X25519 protects today; ML-KEM protects against "store now, decrypt later" by a future quantum computer. |
| ML-KEM code | Native WebCrypto ML-KEM where the browser has it; otherwise `@noble/post-quantum` (audited, no dependencies), version-pinned and served from the NUC with a hash check. Never fetched from a CDN at run time. | WebCrypto support for ML-KEM is still rolling out across browsers. |
| Private key storage | IndexedDB. The ML-KEM secret key is wrapped with a non-extractable AES-KW key, so page script can use it but not export it. | A copied browser profile alone does not give up the keys. |
| Per-file key | A random AES-256-GCM content key for each send. The file is encrypted once, in 4 MB pieces (the engine already used today). | One encryption, however many receiving devices. |
| Piece format | Nonce = 8 random bytes plus the piece number. The AAD is the transfer id, the piece number and a "last piece" flag. | Pieces can't be reordered, swapped between transfers, or cut short. |
| Key wrap per device | For each receiving device: an X25519 shared secret plus an ML-KEM-768 encapsulation, combined with HKDF-SHA-256 (with the transfer id and both device ids) into a key that wraps the content key. | The content key is wrapped once per device. Adding a device means one more small wrap. |
| Names and types | The file name, type and caption travel inside an encrypted manifest. The server sees sizes only. | "The NUC never sees a file or its name." |
| Trust in keys | The NUC hands out public keys, so a hacked NUC could swap one. Defence: trust on first use, plus an alert when a device key changes, plus a short safety number for each pair of people (compare once, Signal-style). | Without this, "end-to-end" depends on trusting the server. |
| Sign-in | The existing SSO (forward_auth). Device registration needs a signed-in session. | Same front door as today. |

## Steps (one at a time; skills before and after each, per PROJECT-STANDARD section 5)

| # | Step | Skills before / after | Done when |
|---|---|---|---|
| 0 | Design + threat model: API, data, crypto review, failure paths | backend-design:design, soundcheck:threat-model / soundcheck:crypto-library-misuse | Design doc approved by Jason |
| 1 | Device registry: keygen in the browser, `/devices` API, safety numbers, key-change alert | backend-design:design / backend-design:audit, soundcheck:cryptographic-failures | Two test devices register; a key change is detected |
| 2 | Encrypted send: reuse the resumable piece engine; a relay store separate from the libraries; per-device key wraps | backend-design:design / backend-design:audit, soundcheck:race-condition | A 2 GB file is sent; the server holds only ciphertext (test greps for plaintext) |
| 3 | Receive: inbox, Accept/Decline, streamed decrypt to disk on phone and PC | frontend-design / ui-ux-pro-max:design | A Playwright test with two browsers round-trips the file byte for byte |
| 4 | Web push: VAPID keys, one subscription per device, payload without names | backend-design:security-discipline / backend-design:audit | The phone gets the notification; Accept works from it |
| 5 | Share sheet + destination picker (people, my devices, library); black/purple/orange theme | frontend-design, ui-ux-pro-max:design / better-accessibility | Install, share and pick work on Android; the library path is unchanged |
| 6 | Expiry (7 days), quotas, sender cancel, audit log without content | backend-design:design / backend-design:audit | The sweep deletes expired relay items; quotas hold under concurrent sends |
| 7 | Project-end security audit | claude-security:scan (Jason starts it), soundcheck:security-review, review-council | No open high findings; crypto choices written down |
| 8 | GitHub + Copilot review, then fixes | — | PR merged |

Rough size: 4-6 sessions of work. Steps 0-1 come first; nothing changes the live drop until Step 2's swap.

## Decisions made here (Jason can change them)

- **Expiry:** 7 days. **Maximum size:** the same as today (INTAKE_MAX_MB, 10 GB).
- **Groups:** none at first; send to several people by picking several.
- **A new device:** gets only transfers sent after it registered. Older ones were wrapped for the devices that existed then.
- **A lost device:** removed from the device list; its pending wraps are deleted.
- **Outside the household:** no link sharing for now (Jason chose people + devices).
