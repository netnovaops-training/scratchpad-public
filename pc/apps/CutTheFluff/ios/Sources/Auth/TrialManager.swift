import Foundation
import Security

/// A fixed number of questions, for somebody with no household account.
///
/// WHAT THIS IS NOT. It is not a second way to sign in. The token it holds
/// opens exactly one route on the server -- the plain model proxy -- and every
/// other route refuses it with a 403. That is enforced on the server, not
/// here: see `Caller` in rust/auth/src/llm.rs. Nothing in this app should hand
/// this token to anything that reads household data.
///
/// THE COUNTER IS THE SERVER'S. What is kept here is only ever the last number
/// the server reported, so it can be shown without spending a question to ask.
/// Deciding locally whether a question is allowed would put the limit inside
/// the IPA, where it is worth nothing -- the same reasoning that keeps the
/// client secret out (see AuthConfig).
///
/// DELIBERATELY IDENTICAL IN SHAPE TO THE ANDROID `TrialManager`, down to the
/// endpoint paths and the stored keys, so the two apps cannot drift into
/// disagreeing about what a trial is.
@MainActor
final class TrialManager: ObservableObject {

    struct Trial: Equatable {
        let email: String
        let quota: Int
        var remaining: Int
        let contact: String
    }

    @Published private(set) var current: Trial?

    /// "/auth/app", not "/app". Caddy mounts the auth service under /auth, so
    /// the route is /auth/app/trial/start. Getting this wrong produces a 404
    /// that looks exactly like a rejected code.
    private static let base = "https://example.org/auth/app"
    private static let defaultContact = "user3@example.com"

    init() {
        if let stored = Self.loadToken(), !stored.isEmpty {
            // Shown from what was cached so the screen has something
            // immediately; `refresh()` then corrects it from the server, which
            // is the side that owns the count.
            current = Trial(
                email: UserDefaults.standard.string(forKey: Keys.email) ?? "",
                quota: UserDefaults.standard.integer(forKey: Keys.quota),
                remaining: UserDefaults.standard.integer(forKey: Keys.remaining),
                contact: UserDefaults.standard.string(forKey: Keys.contact) ?? Self.defaultContact
            )
        }
    }

    var token: String? {
        Self.loadToken().flatMap { $0.isEmpty ? nil : $0 }
    }

    var hasTrial: Bool { token != nil && current != nil }

    func clear() {
        Self.clearToken()
        for key in [Keys.email, Keys.quota, Keys.remaining, Keys.contact] {
            UserDefaults.standard.removeObject(forKey: key)
        }
        current = nil
    }

    func note(remaining: Int) {
        UserDefaults.standard.set(remaining, forKey: Keys.remaining)
        current?.remaining = remaining
    }

    /// Redeem an invite code.
    ///
    /// The server returns the SAME trial for an address that already has one,
    /// so reinstalling the app does not hand out a fresh eight. This therefore
    /// doubles as "get me back into the trial I already had".
    func start(email: String, code: String) async throws {
        var request = URLRequest(url: URL(string: "\(Self.base)/trial/start")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "email": email.trimmingCharacters(in: .whitespacesAndNewlines),
            "code": code.trimmingCharacters(in: .whitespacesAndNewlines),
        ])
        request.timeoutInterval = 20

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw TrialError.message("Could not reach the server.")
        }
        guard (200..<300).contains(http.statusCode) else {
            // The server's own sentence, shown verbatim. It is written for the
            // person reading it, and rewording it here would only make the app
            // and the server disagree.
            let said = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw TrialError.message(said.isEmpty ? "Could not start the trial." : said)
        }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = json["token"] as? String else {
            throw TrialError.message("The server sent something unexpected.")
        }

        let trial = Trial(
            email: json["email"] as? String ?? email,
            quota: json["quota"] as? Int ?? 0,
            remaining: json["remaining"] as? Int ?? 0,
            contact: json["contact"] as? String ?? Self.defaultContact
        )
        Self.saveToken(token)
        UserDefaults.standard.set(trial.email, forKey: Keys.email)
        UserDefaults.standard.set(trial.quota, forKey: Keys.quota)
        UserDefaults.standard.set(trial.remaining, forKey: Keys.remaining)
        UserDefaults.standard.set(trial.contact, forKey: Keys.contact)
        current = trial
    }

    /// Re-check with the server. Costs nothing against the quota.
    func refresh() async {
        guard let token else { return }
        var request = URLRequest(url: URL(string: "\(Self.base)/trial/status")!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return }

        if http.statusCode == 404 {
            // The trial is gone -- expired, or cleared on the server. Dropping
            // it locally is what puts the person back on the sign-in screen
            // rather than into a loop of refusals.
            clear()
            return
        }
        guard (200..<300).contains(http.statusCode),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }

        current = Trial(
            email: json["email"] as? String ?? current?.email ?? "",
            quota: json["quota"] as? Int ?? 0,
            remaining: json["remaining"] as? Int ?? 0,
            contact: json["contact"] as? String ?? Self.defaultContact
        )
        UserDefaults.standard.set(current?.remaining ?? 0, forKey: Keys.remaining)
    }

    // MARK: - Storage

    private enum Keys {
        static let email = "trial.email"
        static let quota = "trial.quota"
        static let remaining = "trial.remaining"
        static let contact = "trial.contact"
    }

    /// The TOKEN goes in the Keychain; the counters go in UserDefaults.
    /// Split on purpose: the token is a bearer credential and belongs where
    /// the SSO session already lives, while "6 questions left" is a display
    /// detail that is not worth a Keychain round trip and reveals nothing.
    private static let keychainAccount = "org.example.cutthefluff.trialtoken"

    private static func saveToken(_ token: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(token.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    private static func loadToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func clearToken() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

enum TrialError: LocalizedError {
    case message(String)
    /// The eight are gone. Its own case, because the screen has to stop
    /// offering a text box rather than show one more error above one.
    case finished(String)

    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        case .finished(let text): return text
        }
    }
}
