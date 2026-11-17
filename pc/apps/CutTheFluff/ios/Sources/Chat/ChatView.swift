import SwiftUI
import PhotosUI
import UIKit

/// The conversation. One screen for a member and for somebody on a trial.
///
/// A TRIAL GETS THE SAME SCREEN, on purpose, exactly as on Android. Somebody
/// deciding whether they want an account should see the thing itself rather
/// than a stripped preview of it. What differs is behind the screen:
/// `trialRemaining` sends the questions to the plain model instead of Hermes,
/// because the server refuses Hermes to a trial token.
struct ChatView: View {
    let client: ChatClient
    /// Absent on a trial: the photo button and the Recipes/Tools links all
    /// reach routes the server refuses to a trial token.
    var vision: VisionClient?
    var onOpenRecipes: (() -> Void)?
    var onOpenTools: (() -> Void)?
    let title: String
    /// Non-nil means this is a trial, and how many questions are left.
    var trialRemaining: Int?
    var onRemaining: (Int) -> Void = { _ in }
    var onFinished: (String) -> Void = { _ in }
    var onNeedsSignIn: () -> Void = {}
    var onLeave: () -> Void = {}

    @State private var history: [ChatMessage] = []
    @State private var draft = ""
    @State private var busy = false
    @State private var error: String?
    /// The photo waiting to be asked about. Held rather than sent at once, so
    /// the question and the picture arrive together -- "what is this?" with no
    /// question attached is a worse prompt than the person would have written
    /// given the chance.
    @State private var pending: UIImage?
    /// Questions the catalogue is waiting on, from the last answer. Kept out
    /// of `history` because they are not part of the conversation -- they are
    /// a thing to do about it, and they leave when done rather than scrolling
    /// away as a transcript would.
    @State private var waiting: [ChatClient.Approval] = []
    @State private var deciding: Int?
    @State private var photoItem: PhotosPickerItem?

    private var onTrial: Bool { trialRemaining != nil }

    var body: some View {
        VStack(spacing: 0) {
            header

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if history.isEmpty {
                            Text(onTrial
                                 ? "Ask it anything. You have \(trialRemaining ?? 0) questions."
                                 : "Ask it anything.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.top, 40)
                        }
                        ForEach(history) { message in
                            bubble(message).id(message.id)
                        }
                        if busy {
                            ProgressView()
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.vertical, 8)
                        }
                        // AFTER THE ANSWER, NOT INSIDE IT. The answer is the
                        // research; these are the questions it raised, and
                        // mixing them would make the reply look like it was
                        // asking for permission.
                        if !waiting.isEmpty && !busy {
                            Text(waiting.count == 1 ? "One thing to confirm"
                                                    : "\(waiting.count) things to confirm")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            ForEach(waiting) { item in
                                approvalCard(item)
                            }
                        }
                    }
                    .padding(16)
                }
                .onChange(of: history.count) { _ in
                    // Follow the conversation down. Without this a long answer
                    // lands entirely below the fold and reads as nothing
                    // having happened.
                    if let last = history.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }

            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 6)
            }

            composer
        }
    }

    private var header: some View {
        HStack {
            Text(title).font(.footnote).foregroundStyle(.secondary)
            Spacer()
            if let left = trialRemaining {
                Text(left == 1 ? "1 question left" : "\(left) questions left")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Sign in", action: onLeave).font(.footnote)
            } else {
                if let onOpenRecipes {
                    Button("Recipes", action: onOpenRecipes).font(.footnote)
                }
                if let onOpenTools {
                    Button("Tools", action: onOpenTools).font(.footnote)
                }
                Button("Sign out", action: onLeave).font(.footnote)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func bubble(_ message: ChatMessage) -> some View {
        let mine = message.role == "user"
        return HStack {
            if mine { Spacer(minLength: 40) }
            // The person's own words are shown literally; the assistant's are
            // Markdown, because that is what it writes whether or not anybody
            // asked. See Markdown.swift.
            Group {
                if mine { Text(message.text) } else { MarkdownText(raw: message.text) }
            }
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(
                    mine ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.12),
                    in: RoundedRectangle(cornerRadius: 14)
                )
                .textSelection(.enabled)
            if !mine { Spacer(minLength: 40) }
        }
    }

    private var composer: some View {
        VStack(spacing: 6) {
            if let pending {
                HStack(spacing: 8) {
                    Image(uiImage: pending)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 42, height: 42)
                        .clipShape(RoundedRectangle(cornerRadius: 7))
                    Text("Photo attached — ask about it.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Remove") { self.pending = nil }.font(.caption)
                }
                .padding(.horizontal, 12)
            }
            HStack(spacing: 8) {
            if vision != nil {
                // PhotosPicker, NOT a camera permission. The system picker
                // hands back only the chosen image and needs no photo-library
                // permission at all, which is the right trade for a button
                // most people press rarely.
                PhotosPicker(selection: $photoItem, matching: .images) {
                    Image(systemName: "photo").font(.title3)
                }
                .disabled(busy)
            }
            // Grows to five lines rather than scrolling one. Both of these
            // are iOS 16, which the deployment target now is -- see the note
            // in project.yml about why it moved.
            TextField("Ask something", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.roundedBorder)
                .disabled(busy)
            Button {
                send()
            } label: {
                Image(systemName: "arrow.up.circle.fill").font(.title2)
            }
            .disabled(busy || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(12)
        .onChange(of: photoItem) { item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self),
                   let image = UIImage(data: data) {
                    pending = image
                }
                photoItem = nil
            }
        }
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        error = nil
        busy = true
        history.append(.user(text))
        /// One waiting question, with a Yes and a No.
    ///
    /// NO DEFAULT AND NO EMPHASIS. Neither button is styled as the expected
    /// answer: "no" is a perfectly good outcome here, and a prominent Yes
    /// beside a plain No is a thumb on the scale.
    @ViewBuilder
    private func approvalCard(_ item: ChatClient.Approval) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.field.replacingOccurrences(of: "_", with: " "))
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tint)
            Text("The source said \u{201C}\(item.raw)\u{201D}.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("Record it as \u{201C}\(item.suggested)\u{201D}?")
                .font(.body)
            if item.confidence > 0 {
                Text("\(Int(item.confidence * 100))% sure")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 10) {
                Button(deciding == item.id ? "Saving\u{2026}" : "Yes") { decide(item, true) }
                    .buttonStyle(.bordered)
                    .disabled(deciding == item.id)
                Button("No") { decide(item, false) }
                    .buttonStyle(.bordered)
                    .disabled(deciding == item.id)
            }
            .padding(.top, 2)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
    }

    private func decide(_ item: ChatClient.Approval, _ yes: Bool) {
        deciding = item.id
        Task {
            do {
                _ = try await client.decide(item.id, approve: yes)
                waiting.removeAll { $0.id == item.id }
                history.append(.assistant(
                    yes
                    ? "Noted \u{2014} \u{201C}\(item.raw)\u{201D} means "
                      + "\u{201C}\(item.suggested)\u{201D} in this catalogue from now on."
                    : "Left alone. Nothing was written, and I'll ask again next "
                      + "time that wording turns up."))
            } catch let e as AuthError {
                if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
            } catch {
                self.error = error.localizedDescription
            }
            deciding = nil
        }
    }

    // history already contains this turn's question, so the prior turns
        // are everything before it.
        let prior = Array(history.dropLast())

        let photo = pending
        pending = nil

        Task {
            do {
                let reply: ChatClient.Reply
                if let photo, let vision {
                    let seen = try await vision.ask(image: photo, question: text, history: prior)
                    // What the vision model saw is shown as its own line. When
                    // an answer about a picture is wrong, this is usually where
                    // it went wrong, and seeing it beats guessing.
                    if !seen.saw.isEmpty {
                        history.append(.assistant("*Looking at the photo: \(seen.saw)*"))
                    }
                    reply = ChatClient.Reply(
                        answer: seen.answer, tools: seen.tools,
                        approvals: seen.approvals)
                } else if onTrial {
                    reply = try await client.plain(text, history: prior)
                } else {
                    reply = try await client.ask(text, history: prior)
                }
                if let left = reply.trialRemaining { onRemaining(left) }
                history.append(.assistant(reply.answer))
                // Replaced, not appended: these are what is waiting NOW.
                // Accumulating would leave a button for a question already
                // answered somewhere else.
                waiting = reply.approvals
            } catch let e as TrialError {
                if case .finished(let said) = e {
                    // Handed upwards rather than shown here: the trial is over,
                    // so the answer is a screen with the address to write to on
                    // it, not an error above a text box that will never work.
                    onFinished(said)
                } else {
                    error = e.errorDescription
                }
            } catch let e as AuthError {
                if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
            } catch {
                // Show the real reason. A generic fallback belongs only to
                // errors that genuinely carry no message.
                self.error = error.localizedDescription
            }
            busy = false
        }
    }
}
