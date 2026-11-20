import SwiftUI
import UIKit

/// The whole app: sign in, or redeem a trial code, then talk to the assistant.
///
/// VERIFICATION IS A GATE, NOT A DESTINATION -- so once a token is proven good
/// the screen becomes the conversation rather than a panel saying "signed in"
/// above nothing. The previous version of this file stopped at proving the
/// token worked, which was the right first milestone and a poor place to stay.
struct ContentView: View {
    @EnvironmentObject private var auth: AuthManager
    @StateObject private var updates = UpdateChecker()
    @StateObject private var trials = TrialManager()

    @State private var entering = false
    @State private var trialOver: String?
    /// Who the SERVER says we are. Fetched rather than assumed: "the sign-in
    /// sheet closed" and "the token works" are different claims, and the
    /// career section is shown by identity, so it has to be the real one.
    @State private var who: Identity?
    @State private var showRecipes = false
    @State private var showTools = false

    var body: some View {
        Group {
            if auth.isSignedIn {
                // A REAL ACCOUNT ALWAYS WINS. Checked before the trial, so
                // somebody who trialled first and was given an account later is
                // never left stuck on the trial.
                member
            } else if let said = trialOver {
                trialFinished(said)
            } else if let trial = trials.current, trials.hasTrial {
                ChatView(
                    client: ChatClient(token: {
                        // `token` is a @MainActor computed property, not a
                        // method, so reading it from here needs the await.
                        guard let token = await trials.token else {
                            throw AuthError.notSignedIn
                        }
                        return token
                    }),
                    title: trial.email,
                    trialRemaining: trial.remaining,
                    onRemaining: { trials.note(remaining: $0) },
                    onFinished: { trialOver = $0 },
                    onNeedsSignIn: { trials.clear() },
                    onLeave: { trials.clear() }
                )
            } else if entering {
                TrialEntryView(
                    trials: trials,
                    onDone: { entering = false },
                    onCancel: { entering = false }
                )
            } else {
                signIn
            }
        }
        .task {
            await updates.check()
            if auth.isSignedIn {
                await loadIdentity()
            } else {
                await trials.refresh()
            }
        }
    }


    /// A signed-in member: the conversation, plus the two screens behind it.
    @ViewBuilder
    private var member: some View {
        // A plain `await` is correct here. AuthManager is @MainActor and
        // freshAccessToken is async, so awaiting it from ChatClient's
        // non-isolated closure hops to the main actor by itself. (An earlier
        // attempt wrapped this in MainActor.run, which would have carried the
        // manager across the boundary rather than the String.)
        let token: () async throws -> String = { try await auth.freshAccessToken() }
        let api = AppApi(token: token)

        if showRecipes {
            RecipeView(
                api: api,
                onBack: { showRecipes = false },
                onNeedsSignIn: { auth.signOut() }
            )
        } else if showTools {
            ToolsView(
                api: api,
                // Shown by identity rather than by a setting, so it cannot be
                // switched on for the wrong person.
                isZee: (who?.email ?? "").lowercased() == "user4@example.com",
                onBack: { showTools = false },
                onNeedsSignIn: { auth.signOut() }
            )
        } else {
            ChatView(
                client: ChatClient(token: token),
                vision: VisionClient(token: token),
                onOpenRecipes: { showRecipes = true },
                onOpenTools: { showTools = true },
                title: who?.display ?? auth.status,
                onNeedsSignIn: { auth.signOut() },
                onLeave: { auth.signOut() }
            )
        }
    }

    private func loadIdentity() async {
        guard let token = try? await auth.freshAccessToken() else { return }
        // A failure here is not fatal and is not announced: the chat works
        // either way, and the only cost is being greeted by the status line
        // instead of by name.
        who = try? await IdentityService.fetch(accessToken: token)
    }

    private var signIn: some View {
        VStack(spacing: 16) {
            // ABOVE THE FOLD, NOT BURIED IN A SETTINGS SCREEN. On Android the
            // app can install the update itself, so a quiet banner is enough.
            // Here the person has to go and do it with a computer, so the
            // prompt has to be somewhere they will actually see it.
            if let update = updates.available {
                Button(action: updates.openDownloadPage) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Version \(update.versionName) is available")
                            .font(.callout.weight(.semibold))
                        Text(update.notes.isEmpty
                             ? "Tap for the update instructions."
                             : update.notes)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Color.accentColor.opacity(0.13), in: RoundedRectangle(cornerRadius: 11))
                }
                .buttonStyle(.plain)
            }

            Text("Cut The Fluff").font(.largeTitle.weight(.semibold))

            Text(auth.status)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Button("Sign in") {
                guard let presenter = Self.topViewController() else { return }
                Task { await auth.signIn(presenting: presenter) }
            }
            .buttonStyle(.borderedProminent)

            Button("Try it with a code") { entering = true }
                .buttonStyle(.bordered)
        }
        .padding(24)
    }

    private func trialFinished(_ said: String) -> some View {
        VStack(spacing: 16) {
            Text(said)
                .font(.callout)
                .multilineTextAlignment(.center)
            // No "try again" button, deliberately. There is nothing to retry:
            // the next thing that helps is an email, and a button that cannot
            // work would only send somebody round the same refusal again.
            Button("Back to sign in") {
                trials.clear()
                trialOver = nil
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(24)
    }

    /// AppAuth needs a real UIViewController to present the auth session
    /// from. SwiftUI does not hand one over, so this walks the active
    /// window scene to find the one actually on screen. Reaching through
    /// the scene rather than caching a reference matters: a stale
    /// controller (from a dismissed sheet, say) makes the browser sheet
    /// fail to present with no visible error.
    static func topViewController() -> UIViewController? {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }

        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController {
            top = presented
        }
        return top
    }
}

/// Email address and invite code.
private struct TrialEntryView: View {
    @ObservedObject var trials: TrialManager
    let onDone: () -> Void
    let onCancel: () -> Void

    @State private var email = ""
    @State private var code = ""
    @State private var busy = false
    @State private var why: String?

    var body: some View {
        VStack(spacing: 14) {
            Text("Enter the code you were sent, and an email address to reach you on.")
                .font(.callout)
                .multilineTextAlignment(.center)

            TextField("Email", text: $email)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.emailAddress)
                // A phone helpfully capitalising the first letter of an address
                // is a support question waiting to happen.
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()

            TextField("Invite code", text: $code)
                .textFieldStyle(.roundedBorder)
                // Codes are printed in capitals. The server normalises anyway,
                // so this matches what they were sent rather than relying on it.
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()

            if let why {
                Text(why)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }

            Button(busy ? "Checking…" : "Start") {
                busy = true
                why = nil
                Task {
                    do {
                        try await trials.start(email: email, code: code)
                        onDone()
                    } catch {
                        busy = false
                        why = error.localizedDescription
                    }
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(busy || email.isEmpty || code.isEmpty)

            Button("Back", action: onCancel).buttonStyle(.bordered)
        }
        .padding(24)
    }
}
