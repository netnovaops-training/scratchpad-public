import Foundation

/// Who the signed-in person is, according to the server rather than the app.
struct Identity: Equatable {
    let email: String
    let name: String

    /// What to greet them with: their name, or the address if none is set.
    var display: String { name.isEmpty ? email : name }
}

/// Asks the household provider who an access token belongs to.
///
/// WHY THIS EXISTS when the app already knows sign-in succeeded. Because "the
/// sign-in sheet closed" and "the token I hold is accepted by the backend" are
/// different claims, and only the second means anything. A token can be
/// well-formed, correctly stored and still rejected -- expired, revoked,
/// issued to the wrong client. Calling a real protected endpoint is the only
/// way to know, and /oidc/userinfo is the one endpoint any OIDC provider has.
///
/// ON THE 401: the provider answers an invalid token with 401 and the body
/// "Token is not valid." That is the signal to sign in again, NOT to retry --
/// nothing about waiting makes a rejected token work.
enum IdentityService {

    static func fetch(accessToken: String) async throws -> Identity {
        var request = URLRequest(url: AuthConfig.issuer.appendingPathComponent("userinfo"))
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AppApiError.message("Could not reach the server.")
        }
        if http.statusCode == 401 { throw AuthError.notSignedIn }
        guard (200..<300).contains(http.statusCode) else {
            throw AppApiError.message("userinfo returned \(http.statusCode).")
        }

        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let name = json["name"] as? String ?? ""
        return Identity(
            email: json["email"] as? String ?? "",
            // `name` is what people.json carries; fall through the other
            // standard claims rather than showing an empty chip.
            name: name.isEmpty ? (json["preferred_username"] as? String ?? "") : name
        )
    }
}
