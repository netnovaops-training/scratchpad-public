package org.example.garden.auth

import android.net.Uri

/**
 * The household OIDC provider, and this app's registration in it.
 *
 * EVERY VALUE HERE HAS A LIVE COUNTERPART, checked before this file was
 * written:
 *   - ISSUER matches rust/auth/src/oidc.rs's `issuer` field exactly --
 *     confirmed 2026-09-17 against the live discovery document at
 *     ISSUER + "/.well-known/openid-configuration".
 *   - CLIENT_ID "garden" is registered in that server's OIDC_CLIENTS
 *     with "public": true and no secret -- there is nothing to put here
 *     that a decompiled APK could leak, because nothing secret exists.
 *   - REDIRECT_URI matches, character for character, one of the two
 *     redirect_uris registered for that client AND the
 *     appAuthRedirectScheme manifest placeholder in app/build.gradle.kts.
 *     The provider matches redirect_uri with a hard string equality (see
 *     oidc.rs's own comment on why: a loose match is how a code gets
 *     handed to somebody else's app), so a drift here is not a bug that
 *     surfaces as a bad login -- it is a login that silently never
 *     returns.
 *
 * WHY NO CLIENT SECRET FIELD EXISTS IN THIS FILE, and never should. This
 * client is registered as a PUBLIC client specifically because a phone app
 * cannot hold a secret -- anyone who downloads the APK can read anything
 * baked in here. AppAuth's token request already omits client
 * authentication for a public client; adding a secret "for safety" would
 * not add safety, it would just be a password shipped to strangers.
 */
object AuthConfig {
    const val ISSUER = "https://example.org/auth/oidc"
    const val CLIENT_ID = "garden"

    /**
     * Custom-scheme redirect. Works on both Android and iOS with no site
     * configuration, which is why it -- not the App Link -- is the one
     * actually wired into AuthorizationService calls today. The App Link
     * (https://example.org/app/oauth2redirect) is already
     * registered server-side for later: switching to it needs
     * assetlinks.json served from the site, which does not exist yet.
     */
    val REDIRECT_URI: Uri = Uri.parse("garden:/oauth2redirect")

    /**
     * openid is required for an id_token at all. email/profile are what
     * this app actually reads: `common::people::find()` on the server
     * keys everything off the email claim, and profile brings the display
     * name so the chat UI has something to greet the person with.
     */
    /// `offline_access` is what asks the provider for a refresh token, and
    /// without it this app is unusable: the access token lives five minutes
    /// and the provider issues nothing to renew it with, so AppAuth fails
    /// near expiry with a null-message exception that surfaces as a blank
    /// error. Clients that do NOT send this scope keep getting exactly the
    /// response they always got, which is why Immich and the rest are
    /// unaffected.
    val SCOPE = "openid email profile offline_access"
}
