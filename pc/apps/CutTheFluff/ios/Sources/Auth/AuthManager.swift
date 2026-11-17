import AppAuth
import Foundation
import SwiftUI

/// The iOS half of the OIDC lifecycle: discover the provider, run the
/// browser-based sign-in, and keep the resulting `OIDAuthState` in the
/// Keychain.
///
/// PKCE IS UNCONDITIONAL, exactly as on Android. `OIDAuthorizationRequest`'s
/// designated initialiser generates a code_verifier/challenge pair by
/// default, and nothing here disables it. That is not a nicety: the server
/// registers this app as a PUBLIC client, where PKCE is the whole of the
/// client's authentication rather than a second layer under a secret. A
/// build that skipped it would not fail loudly; it would produce codes the
/// server refuses to redeem.
///
/// APPAUTH REFUSES EMBEDDED WEB VIEWS, on iOS as on Android. It drives
/// `ASWebAuthenticationSession`, which is what lets the OS guarantee the
/// callback reaches only the app that started the session, even if another
/// installed app claims the same custom scheme. There is no in-app WebView
/// fallback and none should be added.
@MainActor
final class AuthManager: ObservableObject {

    @Published private(set) var status: String = "Not signed in"
    @Published private(set) var isSignedIn: Bool = false

    /// Held for the lifetime of the flow. AppAuth documents that dropping
    /// this reference cancels the in-flight authorization session, which
    /// presents as the browser sheet closing instantly with no error.
    private var currentFlow: OIDExternalUserAgentSession?

    private var authState: OIDAuthState? {
        didSet {
            isSignedIn = authState?.isAuthorized ?? false
            status = isSignedIn ? "Signed in" : "Not signed in"
        }
    }

    init() {
        authState = Self.loadFromKeychain()
        isSignedIn = authState?.isAuthorized ?? false
        status = isSignedIn ? "Signed in" : "Not signed in"
    }

    // MARK: - Sign in

    func signIn(presenting presenter: UIViewController) async {
        status = "Contacting \(AuthConfig.issuer.host ?? "provider")…"
        do {
            let config = try await discoverConfiguration()

            let request = OIDAuthorizationRequest(
                configuration: config,
                clientId: AuthConfig.clientID,
                // nil clientSecret is the correct value for a public
                // client, not an omission. See AuthConfig for why no
                // secret exists to pass.
                clientSecret: nil,
                scopes: AuthConfig.scopes,
                redirectURL: AuthConfig.redirectURI,
                responseType: OIDResponseTypeCode,
                additionalParameters: nil
            )

            let state = try await present(request, on: presenter)
            authState = state
            Self.saveToKeychain(state)
        } catch {
            status = "Sign-in failed: \(error.localizedDescription)"
        }
    }

    private func discoverConfiguration() async throws -> OIDServiceConfiguration {
        try await withCheckedThrowingContinuation { cont in
            // Fetches issuer + "/.well-known/openid-configuration".
            OIDAuthorizationService.discoverConfiguration(forIssuer: AuthConfig.issuer) { config, error in
                if let config {
                    cont.resume(returning: config)
                } else {
                    cont.resume(throwing: error ?? AuthError.discoveryReturnedNothing)
                }
            }
        }
    }

    private func present(
        _ request: OIDAuthorizationRequest,
        on presenter: UIViewController
    ) async throws -> OIDAuthState {
        try await withCheckedThrowingContinuation { cont in
            currentFlow = OIDAuthState.authState(
                byPresenting: request,
                presenting: presenter
            ) { state, error in
                if let state {
                    cont.resume(returning: state)
                } else {
                    cont.resume(throwing: error ?? AuthError.cancelled)
                }
            }
        }
    }

    func signOut() {
        authState = nil
        Self.clearKeychain()
    }

    // MARK: - Tokens

    /// Hands back a usable access token, refreshing only if there is
    /// something to refresh with.
    ///
    /// THE HOUSEHOLD PROVIDER ISSUES NO REFRESH TOKEN. `oidc.rs` says so
    /// outright ("no refresh tokens"), so once the access token expires
    /// there is nothing on record to renew it and this throws. CALLERS MUST
    /// TREAT THAT AS "SIGN IN AGAIN", not as a retryable network fault:
    /// the backend sits behind Caddy `forward_auth`, which answers an
    /// unauthenticated request by copying the auth gateway's HTML login
    /// page back verbatim rather than returning a clean 401. Retrying then
    /// looks like a malformed API response instead of an expired session.
    func freshAccessToken() async throws -> String {
        guard let authState else { throw AuthError.notSignedIn }
        return try await withCheckedThrowingContinuation { cont in
            authState.performAction { accessToken, _, error in
                if let accessToken {
                    cont.resume(returning: accessToken)
                } else {
                    cont.resume(throwing: error ?? AuthError.noAccessToken)
                }
            }
        }
    }

    // MARK: - Keychain

    private static let keychainAccount = "org.example.cutthefluff.authstate"

    /// The session is archived into the Keychain rather than
    /// UserDefaults. UserDefaults is a plist in the app container, readable
    /// from a backup; this token is the whole household SSO session.
    private static func saveToKeychain(_ state: OIDAuthState) {
        guard let data = try? NSKeyedArchiver.archivedData(
            withRootObject: state, requiringSecureCoding: true
        ) else { return }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)

        var add = query
        add[kSecValueData as String] = data
        // Not available until the device has been unlocked once since boot,
        // and never migrated to another device by a backup restore.
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    private static func loadFromKeychain() -> OIDAuthState? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: OIDAuthState.self, from: data)
    }

    private static func clearKeychain() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

enum AuthError: LocalizedError {
    case discoveryReturnedNothing
    case cancelled
    case notSignedIn
    case noAccessToken

    var errorDescription: String? {
        switch self {
        case .discoveryReturnedNothing:
            return "The provider returned neither a configuration nor an error."
        case .cancelled:
            return "Sign-in was cancelled."
        case .notSignedIn:
            return "Not signed in."
        case .noAccessToken:
            return "The session has expired. Sign in again."
        }
    }
}
