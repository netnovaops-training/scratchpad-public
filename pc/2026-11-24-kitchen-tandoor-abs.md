# 2026-11-24: Kitchen -> Tandoor sign-in, and Jason's ABS account made admin

## Kitchen /library (C:\Jasons New Site\www\Kitchen, not committed there)
- Error: "Tandoor's sign-in page did not have the form I expected". Cause, checked live: Tandoor's
  /accounts/login/ now has no form, no CSRF field, only the "Household" SSO button. HIDE_LOGIN_FORM=1
  (docker-compose.web.yml) took effect when tandoor was recreated for the OIDC secret rotation.
  The cookie hand-off was dead anyway: KITCHEN_TANDOOR_PUBLIC is the duckdns host, and a cookie set by
  example.org never reaches it.
- Fix: /library now 303s to `<TANDOOR_PUBLIC>/accounts/oidc/household/login/?process=login&next=<path>`
  (handoff_tandoor.oidc_login_url), the same hand-off the NUC Caddy @needs_handoff block does. No password used.
- Checks: tandoor and auth "tandoor" client secrets match (sha256, values unseen); Tandoor answers that URL
  with a 302 to the issuer with client_id tandoor and the duckdns callback; Tandoor user 1 (superuser) has
  the owner email, users 2-3 the writer emails; /library gives 303 to the SSO URL, no Set-Cookie;
  `next=//evil.com` is dropped. New test: test_library_sends_people_through_tandoor_sso.
- Backups: Kitchen/app/main.py.bak-oidc-20261124-083609, handoff_tandoor.py.bak-oidc-20261124-083609.
- Not tested: the full browser round trip (needs Jason's gate cookie). First click links his SSO identity
  to Tandoor user 1 by email.

## Audiobookshelf (PC container `audiobookshelf`)
- No ABS root login in Pass (title search over all vaults except Cards/Crypto/Wifi: only the OIDC client item).
- DB has TWO accounts with username user1@example.com: the setup-wizard `root` (09-11, has a password, no email)
  and the OIDC-created `user` (11-24). OIDC matches by email, root had none, so OIDC made a second account.
- Done: stopped ABS, backed up /config to D:\archive\2026-11-24\abs\config-before-admin\, set the OIDC
  account's type to admin (1 row, integrity ok), started ABS; verified types are now root + admin.
- OIDC settings: auto register ON, match by email, group claim EMPTY. Our issuer sends no groups claim,
  so ABS cannot map anyone to admin automatically; leave the group claim empty (setting it without the
  claim being sent would block OIDC sign-ins). Empty claim = ABS never changes the type at login, so
  admin sticks.

## Tools
- D:\scratchpad\tools\pass_find_items.py: title match in Pass, found/not found + field length only.
- D:\scratchpad\tools\pass_item_shape.py: one item's JSON shape, field names, string lengths only.
