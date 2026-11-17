# Cut The Fluff

A chat client for the household AI assistant. Sign in with the household
SSO, talk to the assistant, upload a PDF and ask questions about it and get
answers that cite the page they came from.

Two apps, one backend, one identity.

| | Android | iOS |
|---|---|---|
| Language | Kotlin + Jetpack Compose | Swift + SwiftUI |
| Auth | AppAuth-Android 0.11.1 | AppAuth-iOS 1.7+ |
| Built | locally (`./gradlew assembleDebug`) | GitHub Actions macOS runner only |
| Installs by | `adb install` | sideload (AltStore / SideStore / Sideloadly) |

## Why iOS builds only in CI

This project is authored on a Windows machine. Xcode runs only on macOS, so
the iOS target cannot be compiled or even syntax-checked locally. The macOS
runner in `.github/workflows/ios-build.yml` is the only place it is ever
built; a red run there is a compile error, not a CI flake.

The IPA it produces is **unsigned**, deliberately. Signing for a real iPhone
needs a certificate and a provisioning profile carrying that device's UDID,
and both need the $99/yr Apple Developer Program. Unsigned is the correct
output for sideloading, where the free Apple ID on the phone signs at
install time instead. The cost of not paying is a 7-day signature and a
3-app limit, paid in refreshes rather than dollars.

## Identity

Both apps are the same OIDC client, `cutthefluff`, registered on the
household provider (`rust/auth/src/oidc.rs` in the `ai-stack` repo) as a
**public client**: no secret, PKCE required.

A phone app cannot keep a secret. Anything compiled into an APK or IPA is
readable by whoever holds the file, so the server does not pretend
otherwise: it accepts this client with no secret at all, and refuses it
outright if PKCE is absent, at both the authorize and token endpoints.

One string has to match in four places, and the server compares it by exact
equality, so a drift is a sign-in that silently never returns rather than a
visible error:

```
cutthefluff:/oauth2redirect
```

| Where | What sets it |
|---|---|
| Android build | `appAuthRedirectScheme` in `app/build.gradle.kts` |
| Android manifest | `<data android:scheme="cutthefluff"/>` |
| Android source | `AuthConfig.REDIRECT_URI` |
| iOS project | `CFBundleURLSchemes` in `ios/project.yml` |
| iOS source | `AuthConfig.redirectURI` |
| Server | the client's `redirect_uris` in `OIDC_CLIENTS` |

## Known debt

- **`androidx.security:security-crypto` is deprecated** (as of
  `1.1.0-alpha07`) and currently holds the Android OIDC session. Google's
  direction is DataStore + Tink. Still builds, still works; the migration is
  a real rewrite (Protobuf schema, `StreamingAead`, data migration) and has
  not been done.
- **No refresh tokens.** The provider issues none by design, so an expired
  access token means signing in again. Both apps document this at the call
  site; callers must treat the failure as re-auth rather than a retryable
  error, because Caddy's `forward_auth` answers an unauthenticated API call
  with the gateway's HTML login page rather than a clean 401.
- **The iOS app has never run.** It has never been compiled either, until
  the first green CI run. Treat everything in `ios/` as unverified until
  then.

## Building

**Android**, on any machine with the SDK:

```bash
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

**iOS**: push to `main` touching `ios/**`, or run the workflow manually from
the Actions tab. Download the `CutTheFluff-unsigned-ipa` artifact and
sideload it.
