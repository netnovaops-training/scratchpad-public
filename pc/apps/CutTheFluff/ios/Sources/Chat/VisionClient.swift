import Foundation
import UIKit

/// Asks the assistant about a photo.
///
/// THE PHOTO GOES TO /app/ask, NOT TO A VISION MODEL DIRECTLY, exactly as on
/// Android. The server does two stages: the vision model describes the image
/// in words, then that description is handed to Hermes with the question, so
/// the answer comes from something that has the household's memory and tools
/// rather than from a model that can only see. When Hermes gains real image
/// support, nothing in this file changes.
///
/// MEMBERS ONLY. /app/ask is refused to a trial token, so the camera button is
/// not offered on a trial.
struct VisionClient {
    let token: () async throws -> String

    struct Reply {
        let answer: String
        /// What the vision model said it saw. Worth showing: when the answer
        /// is wrong, this is usually where it went wrong, and a person can
        /// see that at a glance instead of guessing.
        let saw: String
        /// Same field as ChatClient.Reply, and this is the path where it
        /// matters most: a photographed nursery tag is exactly the moment
        /// there is something to confirm.
        var approvals: [ChatClient.Approval] = []
        let tools: [String]
    }

    private static let askURL = "https://example.org/auth/app/ask"

    /// The longest edge, in pixels, before the image is scaled down.
    ///
    /// NOT THE FULL-SIZE PHOTO. A modern iPhone shot is 3-8MB, base64 inflates
    /// it by a third, and the vision model gains nothing from pixels beyond
    /// this -- it costs upload seconds on a phone connection to send detail
    /// that is then thrown away.
    private static let maxEdge: CGFloat = 1280
    private static let jpegQuality: CGFloat = 0.82

    func ask(image: UIImage, question: String, history: [ChatMessage]) async throws -> Reply {
        guard let jpeg = Self.encode(image) else {
            throw AppApiError.message("Could not read that image.")
        }

        var request = URLRequest(url: URL(string: Self.askURL)!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // Describing an image and then answering with tools is two model runs.
        request.timeoutInterval = 300
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "text": question,
            "session": "ios",
            "image": "data:image/jpeg;base64,\(jpeg.base64EncodedString())",
            "history": history.suffix(20).map { ["role": $0.role, "content": $0.text] },
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AppApiError.message("Could not reach the assistant.")
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401 { throw AuthError.notSignedIn }
            let said = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw AppApiError.message(
                said.isEmpty ? "The assistant returned \(http.statusCode)." : String(said.prefix(200))
            )
        }

        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let answer = json["answer"] as? String ?? ""
        return Reply(
            answer: answer.isEmpty ? "(no answer came back)" : answer,
            saw: json["saw"] as? String ?? "",
            approvals: ChatClient.Approval.list(from: json),
            tools: json["tools"] as? [String] ?? []
        )
    }

    /// Scale down and re-encode as JPEG.
    static func encode(_ image: UIImage) -> Data? {
        let longest = max(image.size.width, image.size.height)
        guard longest > 0 else { return nil }

        let scale = min(1, maxEdge / longest)
        let target = CGSize(width: image.size.width * scale,
                            height: image.size.height * scale)

        // UIGraphicsImageRenderer redraws the image, which also NORMALISES ITS
        // ORIENTATION. A photo taken in portrait carries an EXIF rotation that
        // jpegData preserves as metadata -- and the server's decoder ignores
        // it, so a sideways picture reaches the vision model and it describes
        // a sideways scene. Redrawing bakes the rotation into the pixels.
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
        return resized.jpegData(compressionQuality: jpegQuality)
    }
}
