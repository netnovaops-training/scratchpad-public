# Passkeys for the household sign-in

A second way to sign in, beside the password. The password sign-in is not
changed (except where an owner or a person switches it off for themselves, see
below). Both work, so nobody is locked out.

Written with AI help (Claude). Not yet tried in a real browser or on a real
phone: see "What is NOT proven" at the bottom.

## What it does

- **One sign-in, one cookie.** A passkey sign-in ends in the very same `sso`
  cookie as a password sign-in (same function, same attributes). Caddy's
  forward_auth (`/verify`), the OIDC apps, `/whoami` and the subdomain cookie
  bridge accept it unchanged; no app changes how it checks. Opening any
  protected page while signed out goes to the sign-in page with the passkey
  prompt, and returns to that page afterwards.
- **The sign-in page** is the approved dark glass look: email, password, Sign
  in, a very small "Lost your passkey device?" link, and the round passkey
  button (the "orb", see "The sign-in page" below). The browser's passkey prompt
  opens by itself only on a device where a passkey has already been used or
  added (see "The opening sequence"); everyone else taps the orb. A cancel never
  triggers a retry, and the password form stays fully usable with a plain line
  saying so. The email box also offers passkeys in its autofill list.
- **Passkeys are made only after signing in**: on the "Add a passkey?" page
  that follows a password sign-in, or on `/auth/account` (any signed-in session,
  including one started with a passkey). There is no way to create one while
  signed out, except the emailed link, which proves the mailbox.
- **Names are automatic; nobody names or renames their own passkey.** There is
  no name box on the offer, account or recovery pages. Each key is saved under
  the person's *label* (what Proton Pass / the phone shows as the account),
  made unique with " 2", " 3" if they have several. The label is, in order: the
  label an owner set for them (Jason's is `Ultra Nova's Passkey`, stored exactly
  as typed, no apostrophe guessing); else `<name> Passkey` from the `name`
  field in `people.json` (that file has one name field, so this is "First Last
  Passkey" or "First Passkey" depending on what is typed there; it is read,
  never changed); else `<part of the email before @> Passkey`. The sign-in
  email is the stable account name. Labels are plain text, at most 64
  characters, never contain "private key", and are escaped on the pages. Only an
  owner can set a label (owner tools, or by hand, see below). Passkeys already
  made keep the name they were created with (the device stores it). (The server
  still accepts a `name` from some other client; the pages never send one.)
- Proton Pass works: registration asks for a discoverable passkey, no
  attestation, no device-type restriction, synced passkeys allowed, user
  verification required, no vendor hints. Proton Pass saves the passkey when its
  extension (or Android autofill) is active at the moment you press Add a
  passkey; if its prompt does not appear, check it is switched on for the site
  and try again.

## Settings (environment, all optional)

| Setting | Default | Meaning |
|---|---|---|
| `AUTH_PASSKEYS` | on | `off` hides and disables everything here |
| `AUTH_PASSKEY_RP_ID` | `example.org` | what a passkey is bound to |
| `AUTH_PASSKEY_ORIGIN` | `https://example.org` | the address in the browser bar |
| `AUTH_PASSKEY_EXTRA_ORIGINS` | none | comma list of extra exact origins (a test host) |
| `AUTH_PASSKEY_SUBDOMAINS` | off | `on` also accepts https on subdomains of the rp id |
| `AUTH_PASSKEY_FILE` | `passkeys.json` in the auth-data folder | where passkeys live |
| `AUTH_PASSKEY_LINK_BASE` | origin + `/auth` | start of the emailed recovery link |
| `AUTH_OWNER_EMAILS` | none | comma list of owner addresses; unset = owner tools off |

Recovery and notice emails use the SMTP settings the service already has
(`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`).

**A passkey is bound to the rp id.** It works on `example.org`, and
(with `AUTH_PASSKEY_SUBDOMAINS=on`) on its subdomains such as
`app.example.org`. It does NOT work on other hosts, for example
`garden.example.org`: `zapto.org` is shared with strangers, so the browser
refuses to treat it as one site. A different host needs its own rp id and its
own enrolment. Change the rp id later and every existing passkey stops working.

## Files (in the auth-data folder)

- `passkeys.json` (mode 600, written atomically, previous copy kept as
  `passkeys.json.bak`). Public keys, counters, names, the random user handle,
  loaner expiry, and per-person settings: `label`, `cap`, `guest`,
  `account_expires`, `passkey_only`, and the answers to "add a passkey?".
  `people.json` is not changed in any way.
- `passkey-recovery.json`: hashed one-time recovery links (SHA-256; the link
  itself is only in the email).
- A damaged `passkeys.json` is never overwritten. Passkeys pause, the password
  keeps working, the log says why. Fix it or restore the `.bak`.
- Entries for people who are no longer in `people.json` are removed by the
  pruning run (counts only in the log). If `people.json` cannot be read, nothing
  is pruned.

## Adding a passkey

1. Sign in (password or passkey). Open `/auth/account`, or accept the "Add a
   passkey?" page after a password sign-in.
2. Choose "Keep it" or a loaner length, type your password again (so a
   borrowed session cannot add one; asked once, at this step), press Add, follow
   the device prompt. The page says what the key will be called; you do not type
   a name.
3. Do it once for each device or manager. Two lasting passkeys is a sensible
   minimum. Each person can hold 5 by default (the owner can change that).

## The offer after a password sign-in

After a successful PASSWORD sign-in (and right after a new person chooses their
first password, or finishes an emailed password reset), anyone with no passkey
who has not said no sees one short page: "Add a passkey on this device so you
can sign in with your fingerprint or face?"

- **Add a passkey** shows the name and password boxes there, then carries on.
- **Not now** asks again in 7 days. **Don't ask again** never asks again (it
  can still be done from `/auth/account`).
- The session is already signed in when the page appears, and the place the
  person was heading (forward_auth page, OIDC return, a subdomain through the
  cookie bridge) is kept: every button and the "Skip this time" link end there.
  A browser without passkey support skips straight through. If passkeys are off
  or `passkeys.json` is damaged there is no offer. Guests are not offered.
- Existing accounts get it at their next password sign-in.

## Loaner passkeys, caps, guests

- **Loaner:** choose 1, 7 or 30 days when adding. After that time the service
  refuses it at once and deletes it at the next pruning run (at startup and
  every 15 minutes).
- **Cap:** most passkeys per person, default 5, owner-settable 1 to 20. Full
  gives a plain message ("...ask Jason to raise the limit").
- **Guest (employer, visitor):** the owner marks a person as a guest with a last
  day. A guest may hold ONE temporary passkey (1, 7 or 30 days, never longer than
  the account). From the end of the last day every way in is refused for them:
  password, passkey and the cookie they already hold (checked fresh on every
  request, nothing cached), no recovery link is sent, and their passkeys are
  removed at the next pruning run. Marking someone as a guest removes any
  passkeys they already had.

## Passkey-only (optional)

On the account page you can switch password sign-in off for your own account.
It only switches on while you hold at least two lasting passkeys (loaners do
not count) and switches back on from the same page. If the guard stops holding
(keys removed, loaners expired, "lost device"), password sign-in is simply back.
The sign-in page gives the normal "don't match" message for a passkey-only
account, so it does not reveal who has the switch on.

## Lost a device, or a new one: the email link

- Sign-in page, **Lost your passkey device?** Type your email. Whatever you
  type, the page says the same thing, at the same speed: the work (reading the
  list, writing the file, sending the mail) happens after the reply.
- A known account gets a link: single use, 15 minutes, stored hashed. While one
  link is still live, asking again does NOT replace it (so a stranger typing your
  address cannot keep cancelling your link) and sends nothing new; wait for it to
  expire. At most 3 requests per address and 20 per client address per hour.
- The link opens a page that can do exactly one thing: add a passkey. It does
  not sign anyone in. Opening the page does not use the link up (mail scanners
  open links); starting the add does.
- An email tells the account when a passkey is added or removed.
- **On the account page:** "Keep only this" beside the passkey in your hand
  removes all the others; "Remove all my passkeys" removes everything. Both also
  sign every device out. After removing all, use the email link.

**Threat note:** the email account is now the recovery key. Anyone who controls
the mailbox can add a passkey to the account. Protect that mailbox as strongly
as the passwords (strong password, two-step sign-in, a passkey of its own).

## Passwords and passkeys together

- **Emailed password reset** (the "forgotten password" flow): every passkey for
  that person is removed and passkey-only is cleared. A reset means the mailbox
  or the old password may be the problem. They add passkeys again, and the "Add
  a passkey?" page returns. Their label, cap and guest settings stay.
- **Normal password change:** passkeys are kept; passkey-only is switched off
  only if the passkeys behind it are no longer enough.
- A passkey sign-in needs a password on the account and no pending forced
  password change (the session cookie is stamped with the password, so a
  person with no password cannot hold a session). A person who is flagged to
  choose a new password signs in with the password first.

## Owner tools

Set `AUTH_OWNER_EMAILS=jason@example.com` (comma list). Only those signed-in
people see **Owner tools** on `/auth/account` and can open `/auth/owner`. It
lists everyone from `people.json` and, for every passkey, which person it
belongs to, when it was created, when it was last used (date and time, UTC), its
expiry, and a device hint where the service saw one: the browser and system as a
short family ("added from Chrome on Android; last used from Safari on iOS via
example.org"), taken from the User-Agent and the site address the
sign-in came from. Only that family is stored: no versions, device models or IP
addresses. It never shows credential ids or keys (the "Revoke" buttons use a
short random handle). The owner can revoke one passkey, revoke all of a
person's, set a person's passkey label (the name their keys carry in the
passkey manager; empty goes back to automatic), set the cap, and mark/unmark a
guest with a last day. Whenever ANY person adds a passkey, every address in
`AUTH_OWNER_EMAILS` gets an email: "<Name> added a passkey on <date>" (through
the same SMTP settings; with none configured it is logged that nothing was
sent). People still see only their own keys on `/auth/account`, with no device
lines.

**Every change needs a fresh passkey assertion from the owner's OWN passkey**
(user verification, i.e. fingerprint/face): the page asks the server to begin
the action, the owner's device signs a challenge limited to the owner's own
passkeys, and only then is the action run. A stolen session cookie alone cannot
touch anyone's passkeys. So the owner needs at least one passkey of their own
first. Revoking takes effect on the very next request (sign-in reads the file
fresh each time); revoking all also ends that person's sessions. Everyone else
sees only their own passkeys on `/auth/account`. Each action leaves an audit
line naming the owner, the person and the passkey name.

Without `AUTH_OWNER_EMAILS` these tools are off. The same things can be done by
editing `passkeys.json` by hand (stop the container or edit between requests;
keep it valid JSON). A person's entry looks like:

```json
{ "users": [ { "email": "jason@example.com",
               "label": "Ultra Nova's Passkey",
               "cap": 8,
               "guest": false } ] }
```

For a guest add `"guest": true, "account_expires": 1798761600` (unix seconds;
that is the moment the account ends). `user_id` can be left out of a hand-made
entry; it is filled in when the first passkey is added.

## Rate limits and safety

- Limits are per client address (Caddy sets `X-Forwarded-For`; the right-most
  entry is used, junk becomes one shared "unknown" bucket), never one shared
  bucket for the whole site, so a stranger cannot use up everyone's allowance:
  passkey sign-in start 60/minute, failed sign-ins 20 per 5 minutes (plus 3 per
  5 minutes per account), recovery requests 3/hour per address and 20/hour per
  client address, bad recovery links 20 per 5 minutes. A credential that is not
  even readable counts toward nothing. Add a passkey: 10 per 5 minutes per
  account, and a wrong password there counts toward the same lockout as the
  sign-in form. Note: if Docker's network translation hides the real client
  address (see `main.rs`), all visitors look like one address and these become
  site-wide again.
- A started sign-in, enrolment or owner action lives in memory for 5 minutes and
  works once. A restart just means "try again".
- Every passkey request, the recovery form included, must carry an `Origin`
  header matching the configured origins, plus the session where one is needed.
- The `next` (return-to) address must be a same-site path: backslashes and
  control characters are refused (`/\evil.com` is read by browsers as
  `//evil.com`), both on the server and in the page script.
- Logs name people and passkey names only: never credential ids, tokens or
  links. With no SMTP configured, the service logs that a message was not sent,
  never its text.

## The sign-in page: look, the orb, the phone layout

**Background (all pages):** the approved template's navy radial sky with the two
drifting blurred glows (violet top-left, blue bottom-right) at "Aurora"
strength (opacity 0.55), pure CSS: no starfield and no script for it. The accent
is violet `#8b7cff` with dark ink on accent buttons, on a dark glass card. The
shared stylesheet is `templates/auth.css` (served at `/auth/static/auth.css`).
System fonts only, nothing is fetched from other sites. Reduced motion stops the
drift.

**The orb is the passkey button.** A round button about 172 px wide, labelled
"Tap to sign in with your passkey" underneath, keyboard-focusable with a visible
focus ring and the accessible name "Sign in with your passkey". Inside it is a
short silent looping clip (`static/passkey-orb.v1.mp4`, 360x360, about 19.9 s,
367 KB; it plays forward then in reverse, so plain `loop` rocks it back and
forth with no script), masked so its edges fade into the card, over a CSS glow
that stands in when the clip is not loaded. It starts playing as soon as the
page loads (muted, `playsinline`, `autoplay`). It is not loaded at all when the
device asks for reduced motion or data saving, and it is hidden if it fails to
load or play; the button works either way. The clip is embedded in the program
and served at `/auth/static/passkey-orb.v1.mp4` with the right type, byte-range
support and `Cache-Control: public, max-age=31536000, immutable`. The `.v1` in
the name is the version: to swap the clip (a smaller WebM, a new animation), put
the new file in `static/` under a new name such as `passkey-orb.v2.mp4`, change
the `include_bytes!` line, the match arm in `static_file` and the `data-src` in
`templates/signin.html`, and rebuild; browsers fetch the new name instead of
waiting out a year-long cache.

**Phone layout (narrow, portrait, at least 640 px tall):** the page never
scrolls (`100dvh`, `overflow: hidden`, compact spacing). The card (logo, email,
password, Sign in, the lost-device link) sits at the top, and the orb is parked
where an under-display fingerprint sensor usually is: horizontally centred, its
centre about 78% of the way down (`22dvh` up from the bottom, never below the
safe area), so the thumb goes straight from the orb to the sensor. On a short
screen (under 640 px tall) or a wide one, the orb sits inside the card instead.
The system passkey sheet is drawn by Android and is opaque, so the orb is
visible before and after the prompt and around it, not through it. (This was
checked by reading the stylesheet, not on a phone.)

## The opening sequence (only where a passkey is already in use)

The page cannot know who has a passkey before sign-in, so after any successful
passkey sign-in or enrolment on a device, that browser stores a non-secret note
in `localStorage` (`pk-here` = `1`, always inside `try/catch`; it holds no
credential). It is cleared by "lost device", and when the server says the
passkey is not valid (a 401). With the note:

1. the page fades in (a tiny script in the page head marks it before first
   paint, so there is no flash);
2. the orb video fades and scales in (0.92 to 1, about 500 ms) once its first
   frame is ready (`requestVideoFrameCallback`, else the `playing` event);
3. a light sweeps diagonally across the orb once (about 900 ms) and a soft outer
   glow pulses once. The sweep is a layer UNDER the video, 125% of the orb's
   size, and the video above it uses a screen blend, so the light shows around
   the faded edge and through the darker parts of the video while the video
   itself stays unobscured;
4. the passkey prompt opens `FIRST_FRAME_TO_PROMPT_MS` (300 ms) after the first
   frame, and never later than `MAX_WAIT_MS` (1200 ms) after the page started
   loading (a slow or failed video does not hold it up).

Both delays are constants at the top of `templates/passkey.js` so they can be
tuned from a timing test. Without the note: the plain page; the orb still plays
but nothing opens by itself and there is no sweep; the prompt opens when they
tap the orb. Reduced motion: no scale, no sweep, no pulse, no page fade; the
prompt timing is unchanged. If the browser blocks storage, the page behaves as
for someone without the note.

## Forcing everyone to sign in again (optional; NOT done by the code)

The offer only appears when someone signs in with a password, and sessions last
180 days, so people who stay signed in will not see it for a long time. How
sessions work here (from `src/main.rs`): the `sso` cookie holds the email, issue
and expiry times and a short fingerprint of the person's password hash, signed
with HMAC-SHA256 using `AUTH_SECRET`. Nothing is stored server side except a
small `revoked.json` of "everything before this time is void" per person.

1. **Everyone at once:** put a new random value in `AUTH_SECRET` (`auth.nuc.env`
   on the NUC; `auth-setup.ps1` makes one) and recreate `auth-nuc`. Only the auth
   service uses that value. Every existing cookie then fails its signature check.
2. **One person:** they press "sign out everywhere", or add their email to
   `revoked.json` with the current time, or change their password.

What it does to the household: each person is signed out of every browser and
phone at once, meets the sign-in page on their next visit to anything behind it,
signs in again once and sees the passkey offer. Passwords and passkeys are
untouched, so nobody is locked out; have the passwords to hand. Pages open at
that moment bounce to sign-in on the next click. Apps that keep their own login
after the OIDC hand-off (Immich and its phone app) keep it until they next ask
auth. Pick a quiet time and tell them first.

## Roll back

- Turn passkeys off, keeping everything else: set `AUTH_PASSKEYS=off`, recreate
  the container. Or delete `passkeys.json` in the auth-data folder (all
  passkeys, labels, caps and guest settings gone; passwords unaffected;
  `people.json` untouched).
- Go back to the old build: the old image is kept as `ai-stack/auth:before-passkeys`
  (step 1 below). Re-tag it and recreate the container.

## Deploy steps for Jason (nothing here has been run)

On the NUC, from the repo copy that has these changes (`rust/Dockerfile` now
installs `libssl3` in the final image, which the passkey library needs):

1. Keep the old image:
   `docker tag ai-stack/auth:latest ai-stack/auth:before-passkeys`
2. Build the new one (the Dockerfile takes the app name):
   `docker build -f rust/Dockerfile --build-arg APP=auth -t ai-stack/auth:latest rust`
   (use the same build command you used last time if it differs).
3. In `auth.nuc.env` add `AUTH_OWNER_EMAILS=<your sign-in email>` to use the
   owner tools; add `AUTH_PASSKEY_SUBDOMAINS=on` only if you want subdomains.
   Everything else has a working default.
4. Recreate the container: `docker compose up -d --force-recreate auth-nuc`
   (use the compose file and service name you normally use).
5. Check: `docker logs auth-nuc` shows `passkeys ready`; the sign-in page shows
   the new look and the button; sign in with your password, accept the offer or
   open `/auth/account`, add a passkey (set your label to `Ultra Nova's Passkey`
   first), sign out, sign in with it.
6. Roll back with the section above if anything is off.

## What is NOT proven

The tests drive the real handlers with a software authenticator (a library
fake), so the server side is exercised end to end, including owner confirmation
by passkey. Not covered: a real browser, a real phone, Proton Pass, Windows
Hello, the page scripts (prompt, autofill, the orb video, the opening sequence
and its timings, owner buttons), how the pages look (the phone layout and the
no-scroll fit were checked by reading the stylesheet only), real email delivery
(including the owner notices), and building the Docker image (a release
build of the crate does compile). The software device cannot make discoverable
keys or return a user handle, so the tests give it the allowed credential and add
the user handle the way a real authenticator would. Try it on a test host first
(`AUTH_PASSKEY_RP_ID`, `AUTH_PASSKEY_ORIGIN`).
