import Foundation

/// Talks to the assistant through the auth service.
///
/// TWO ROUTES, AND WHICH ONE IS USED IS NOT A PREFERENCE.
///
///   `ask`   -> /auth/app/ask, which reaches Hermes: the per-person memory
///              store and the household tools. For a signed-in member.
///   `plain` -> /auth/app/chat, an ordinary OpenAI-shaped proxy that has
///              never met anybody and can look nothing up. For a TRIAL.
///
/// A trial token sent to `ask` gets a 403 from the server, by design -- see
/// TrialManager for why. So this is not a fallback or a degraded mode; it is
/// the honest shape of a trial. Somebody deciding whether they want an account
/// is not owed the contents of this house in the meantime.
///
/// THE APP NEVER SAYS WHO IT IS. It sends whichever bearer token it holds and
/// the auth service turns that into an identity server-side. A client able to
/// name the person could read anything anybody has told the assistant.
///
/// DELIBERATELY MIRRORS THE ANDROID `ChatClient`, including not streaming:
/// Hermes answers in one piece after it has finished using tools, and faking a
/// stream by revealing a finished string adds latency to pretend at liveness.
struct ChatClient {

    /// Returns a bearer token. The closure is the seam that lets the same
    /// screen serve a member and a trial without knowing which it has.
    let token: () async throws -> String

    struct Reply {
        let answer: String
        let tools: [String]
        /// Questions left on a trial, from the server's X-Trial-Remaining
        /// header. RETURNED RATHER THAN HANDED TO A CALLBACK: a closure fired
        /// from inside an async send has to hop back to the main actor to
        /// touch any view state, which is a Task and a Sendable argument for
        /// the sake of one integer the caller is already awaiting.
        var trialRemaining: Int? = nil
        /// Questions the garden catalogue is waiting on, if this turn raised
        /// any. BUILT BY THE SERVER FROM THE DATABASE, never parsed out of
        /// the answer text -- a button has to mean exactly one thing, and a
        /// button the model wrote means whatever the model meant.
        var approvals: [Approval] = []
    }

    /// One waiting question, shaped for a Yes/No.
    ///
    /// Both strings are carried and both are shown: approving the tidy
    /// version without seeing what a tag actually said is approving a
    /// translation nobody was shown.
    struct Approval: Identifiable, Hashable {
        let id: Int
        let field: String
        let raw: String
        let suggested: String
        let confidence: Double
        let source: String

        static func list(from json: [String: Any]) -> [Approval] {
            (json["approvals"] as? [[String: Any]] ?? []).compactMap { row in
                guard let id = row["id"] as? Int,
                      let suggested = row["suggested"] as? String,
                      !suggested.isEmpty else { return nil }
                return Approval(
                    id: id,
                    field: row["field"] as? String ?? "",
                    raw: row["raw"] as? String ?? "",
                    suggested: suggested,
                    confidence: row["confidence"] as? Double ?? 0,
                    source: row["source"] as? String ?? ""
                )
            }
        }
    }

    private static let base = "https://example.org/auth/app"

    func ask(_ question: String, history: [ChatMessage]) async throws -> Reply {
        var request = URLRequest(url: URL(string: "\(Self.base)/ask")!)
        request.httpMethod = "POST"
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "text": question,
            "session": "ios",
            // Prior turns travel with the request: the service is stateless,
            // so it can restart mid-conversation without losing the thread.
            "history": history.suffix(20).map { ["role": $0.role, "content": $0.text] },
        ])
        // Tool use is slow. A shelf search plus a model turn can run for a
        // couple of minutes, and cutting it off looks identical to being
        // ignored.
        let (data, _) = try await send(request, timeout: 300)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let answer = json["answer"] as? String ?? ""
        return Reply(
            answer: answer.isEmpty ? "(no answer came back)" : answer,
            tools: json["tools"] as? [String] ?? [],
            approvals: Approval.list(from: json)
        )
    }

    /// Answer one of the catalogue's waiting questions.
    ///
    /// THE BODY IS AN ID AND A YES OR NO. It deliberately does not carry the
    /// value: the server reads that from the row, so a tampered request
    /// cannot approve a different word than the button showed. Identity
    /// comes from the token for the same reason.
    @discardableResult
    func decide(_ id: Int, approve: Bool) async throws -> String {
        var request = URLRequest(url: URL(string: "\(Self.base)/garden/decide")!)
        request.httpMethod = "POST"
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["id": id, "approve": approve])
        let (data, _) = try await send(request, timeout: 45)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let status = json["status"] as? String ?? ""
        return status.isEmpty ? (approve ? "approved" : "rejected") : status
    }

    /// The plain model. `onRemaining` is fed from the X-Trial-Remaining header
    /// the server sets, so the count shown always comes from the side that
    /// owns it.
    func plain(_ question: String, history: [ChatMessage]) async throws -> Reply {
        var messages = history.suffix(20).map { ["role": $0.role, "content": $0.text] }
        messages.append(["role": "user", "content": question])

        var request = URLRequest(url: URL(string: "\(Self.base)/chat")!)
        request.httpMethod = "POST"
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": "chat",
            "messages": messages,
            "stream": false,
        ])

        let (data, remaining) = try await send(request, timeout: 120)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let answer = ((json["choices"] as? [[String: Any]])?.first?["message"]
            as? [String: Any])?["content"] as? String ?? ""
        return Reply(
            answer: answer.isEmpty ? "(no answer came back)" : answer,
            tools: [],
            trialRemaining: remaining
        )
    }

    private func send(
        _ base: URLRequest,
        timeout: TimeInterval
    ) async throws -> (Data, Int?) {
        var request = base
        request.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = timeout

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw TrialError.message("Could not reach the assistant.")
        }

        let said = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        switch http.statusCode {
        case 200..<300:
            return (data, http.value(forHTTPHeaderField: "X-Trial-Remaining").flatMap(Int.init))
        case 401:
            throw AuthError.notSignedIn
        case 402:
            // The trial running out. The server's sentence already names the
            // address to write to.
            throw TrialError.finished(said)
        default:
            throw TrialError.message(
                said.isEmpty ? "The assistant returned \(http.statusCode)." : String(said.prefix(200))
            )
        }
    }
}

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let role: String
    let text: String

    static func user(_ text: String) -> ChatMessage { ChatMessage(role: "user", text: text) }
    static func assistant(_ text: String) -> ChatMessage { ChatMessage(role: "assistant", text: text) }
}
