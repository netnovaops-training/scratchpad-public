import Foundation

/// The household OIDC provider, and this app's registration in it.
///
/// DELIBERATELY IDENTICAL TO THE ANDROID `AuthConfig.kt`. Same issuer, same
/// client, same redirect, same scopes. One client registration serves both
/// apps because the server does not care which platform presents the code,
/// only that the redirect_uri matches exactly and PKCE verifies.
///
/// EVERY VALUE HERE HAS A LIVE COUNTERPART, checked before this file was
/// written:
///   - `issuer` matches `rust/auth/src/oidc.rs`'s configured issuer, and was
///     confirmed against the live discovery document.
///   - `clientID` "cutthefluff" is registered there with `"public": true`
///     and NO secret.
///   - `redirectURI` is one of the two redirect_uris registered for that
///     client, and matches the `cutthefluff` URL scheme declared in
///     `ios/project.yml` under CFBundleURLTypes. The server compares
///     redirect_uri by exact string equality, so a mismatch does not
///     produce an error page, it produces a sign-in that silently never
///     returns.
///
/// THERE IS NO CLIENT SECRET HERE, AND THERE MUST NEVER BE. This is a
/// public client precisely because a phone app cannot keep a secret:
/// anything compiled into the IPA is readable by anyone who has the IPA.
/// PKCE is what authenticates this client, and the server REQUIRES it for
/// public clients rather than merely accepting it.
enum AuthConfig {
    static let issuer = URL(string: "https://example.org/auth/oidc")!
    static let clientID = "cutthefluff"

    /// Custom-scheme redirect. Chosen over the registered App Link / universal
    /// link because a universal link needs an apple-app-site-association file
    /// served from the domain, which does not exist yet. The custom scheme
    /// needs no server-side file and works today.
    static let redirectURI = URL(string: "cutthefluff:/oauth2redirect")!

    /// `openid` is required to get an id_token at all. `email` is what the
    /// server keys identity off (`common::people::find()` looks up by email),
    /// and `profile` carries the display name the UI greets the person with.
    static let scopes = ["openid", "email", "profile"]
}
