import Foundation
import UIKit

/// The non-chat parts of the household API: shelves, uploads, recipes, the
/// career list and the support note.
///
/// EVERY ROUTE HERE IS MEMBERS-ONLY. The auth service refuses all of them to a
/// trial token with a 403, deliberately -- see rust/auth/src/llm.rs. So this
/// type should only ever be built with a real OIDC token, and the screens that
/// use it are not offered to a trial.
///
/// MIRRORS THE ANDROID `AppApi`, endpoint for endpoint and field for field, so
/// a change to the server has one obvious counterpart on each side rather than
/// two shapes that drift.
struct AppApi {
    let token: () async throws -> String

    private static let base = "https://example.org/auth/app"

    struct Shelf: Identifiable, Hashable {
        var id: String { path }
        let path: String
        let label: String
        let sharedWith: [String]
    }

    struct UploadResult {
        let saved: [String]
        let skipped: [String]
        let note: String
    }

    struct Award: Identifiable, Hashable {
        let id: String
        let name: String
        let tier: String
        let status: String
    }

    struct RecipeSummary: Identifiable, Hashable {
        let id: String
        let name: String
        let course: String
    }

    struct Recipe {
        let id: String
        let name: String
        let course: String
        let ingredients: [String]
        let steps: [String]
        /// "fallback" means the model was unreachable and the split is rough.
        let parsedBy: String
    }

    // MARK: - Shelves

    func shelves() async throws -> [Shelf] {
        let json = try await getJSON("\(Self.base)/upload-targets")
        let targets = json["targets"] as? [[String: Any]] ?? []
        return targets.map {
            Shelf(
                path: $0["path"] as? String ?? "",
                label: $0["label"] as? String ?? "",
                sharedWith: $0["shared_with"] as? [String] ?? []
            )
        }
    }

    // MARK: - Upload

    /// Sends one file to a shelf as multipart/form-data.
    ///
    /// The body is assembled in memory, unlike Android's streamed version.
    /// That is a deliberate difference, not an oversight: the iOS document
    /// picker is used for PDFs and documents rather than the hundreds-of-
    /// megabytes textbooks that drove the streaming there, and a streamed
    /// multipart body on URLSession needs an InputStream-backed body task that
    /// is a great deal of machinery for files this size. If that changes, this
    /// is the function to revisit.
    func upload(shelf: String, fileURL: URL, filename: String) async throws -> UploadResult {
        // Security-scoped access: a URL from the document picker is not
        // readable without it, and the failure is a silent empty read.
        let scoped = fileURL.startAccessingSecurityScopedResource()
        defer { if scoped { fileURL.stopAccessingSecurityScopedResource() } }

        let data = try Data(contentsOf: fileURL)
        let boundary = "Boundary-\(UUID().uuidString)"

        var body = Data()
        func append(_ string: String) { body.append(Data(string.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"shelf\"\r\n\r\n")
        append("\(shelf)\r\n")
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: application/octet-stream\r\n\r\n")
        body.append(data)
        append("\r\n--\(boundary)--\r\n")

        var request = URLRequest(url: URL(string: "\(Self.base)/upload")!)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        request.httpBody = body

        let json = try await send(request, timeout: 600)
        // `skipped` is an array of OBJECTS carrying name and why, not strings.
        // The server explains why each file was refused, and showing only a
        // count would hide "arrived empty" -- the one a person can act on.
        let skipped: [String] = (json["skipped"] as? [Any] ?? []).map { entry in
            if let o = entry as? [String: Any] {
                return "\(o["name"] as? String ?? "that file"): \(o["why"] as? String ?? "refused")"
            }
            return String(describing: entry)
        }
        return UploadResult(
            saved: json["saved"] as? [String] ?? [],
            skipped: skipped,
            note: json["note"] as? String ?? ""
        )
    }

    // MARK: - Career

    func awards() async throws -> [Award] {
        let json = try await getJSON("\(Self.base)/awards")
        let items = json["awards"] as? [[String: Any]] ?? []
        return items.enumerated().map { index, a in
            let id = a["id"] as? String ?? "\(index)"
            let name = a["name"] as? String ?? ""
            let status = a["status"] as? String ?? ""
            return Award(
                id: id,
                // Same fallbacks as Android: some entries carry only an id, or
                // call the state "kind" rather than "status". A blank line in
                // a list of awards is worse than a slightly odd label.
                name: name.isEmpty ? id : name,
                tier: a["tier"] as? String ?? "",
                status: status.isEmpty ? (a["kind"] as? String ?? "") : status
            )
        }
    }

    // MARK: - Recipes

    func recipes(query: String = "") async throws -> [RecipeSummary] {
        let encoded = query.addingPercentEncoding(
            withAllowedCharacters: .urlQueryAllowed) ?? ""
        let json = try await getJSON("\(Self.base)/recipes?q=\(encoded)")
        let items = json["recipes"] as? [[String: Any]] ?? []
        return items.enumerated().map { index, r in
            RecipeSummary(
                id: r["id"] as? String ?? "\(index)",
                name: r["name"] as? String ?? "",
                course: r["course"] as? String ?? ""
            )
        }
    }

    func recipe(id: String) async throws -> Recipe {
        // Long, because an unparsed recipe is split by the model on first
        // view. It is cached server-side afterwards, so only the first person
        // to open one waits.
        let json = try await getJSON("\(Self.base)/recipe/\(id)", timeout: 300)
        return Recipe(
            id: json["id"] as? String ?? id,
            name: json["name"] as? String ?? "",
            course: json["course"] as? String ?? "",
            ingredients: (json["ingredients"] as? [String] ?? []).filter { !$0.isEmpty },
            steps: (json["steps"] as? [String] ?? []).filter { !$0.isEmpty },
            parsedBy: json["parsed_by"] as? String ?? ""
        )
    }

    // MARK: - Support

    func support(kind: String, text: String) async throws -> String {
        var request = URLRequest(url: URL(string: "\(Self.base)/support")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["kind": kind, "text": text])
        let json = try await send(request, timeout: 60)
        let note = json["note"] as? String ?? ""
        return note.isEmpty ? "Sent." : note
    }

    // MARK: - Shared

    private func getJSON(_ url: String, timeout: TimeInterval = 60) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: url)!)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await send(request, timeout: timeout)
    }

    private func send(_ base: URLRequest, timeout: TimeInterval) async throws -> [String: Any] {
        var request = base
        request.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = timeout

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AppApiError.message("Could not reach the server.")
        }
        switch http.statusCode {
        case 200..<300:
            return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        case 401:
            throw AuthError.notSignedIn
        default:
            let said = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw AppApiError.message(
                said.isEmpty ? "The server returned \(http.statusCode)." : String(said.prefix(240))
            )
        }
    }
}

enum AppApiError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        }
    }
}
