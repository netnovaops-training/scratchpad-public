import SwiftUI
import UniformTypeIdentifiers

/// Everything that is not the conversation: adding a document, the career
/// list, and writing to Jason.
///
/// MEMBERS ONLY. Every route behind this screen is refused to a trial token,
/// so it is not offered on one.
struct ToolsView: View {
    let api: AppApi
    /// The career section is hers. Decided by identity rather than by a
    /// setting, so it cannot be switched on for the wrong person.
    let isZee: Bool
    let onBack: () -> Void
    let onNeedsSignIn: () -> Void

    @State private var shelves: [AppApi.Shelf] = []
    @State private var chosen: String = ""
    @State private var picking = false
    @State private var awards: [AppApi.Award] = []
    @State private var awardsLoaded = false
    @State private var supportKind = "Problem"
    @State private var supportText = ""
    @State private var busy = false
    @State private var note: String?
    @State private var error: String?

    private let kinds = ["Problem", "Idea", "Question"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let note {
                    Text(note)
                        .font(.footnote)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.accentColor.opacity(0.13),
                                    in: RoundedRectangle(cornerRadius: 10))
                }
                if let error {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }

                // ---------------------------------------------------- upload
                Text("Add a document").font(.headline)
                if shelves.isEmpty {
                    Text("No shelf is set up for you yet.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Shelf", selection: $chosen) {
                        ForEach(shelves) { shelf in
                            Text(shelf.label.isEmpty ? shelf.path : shelf.label)
                                .tag(shelf.path)
                        }
                    }
                    .pickerStyle(.menu)

                    if let shelf = shelves.first(where: { $0.path == chosen }),
                       !shelf.sharedWith.isEmpty {
                        // WHO ELSE CAN SEE IT, before the file is sent rather
                        // than after. A shared shelf is a reasonable thing to
                        // have and a terrible thing to discover.
                        Text("Also visible to \(shelf.sharedWith.joined(separator: ", ")).")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Button("Choose a file") { picking = true }
                        .buttonStyle(.bordered)
                        .disabled(busy || chosen.isEmpty)
                }

                Divider()

                // ---------------------------------------------------- career
                if isZee {
                    Text("Career options").font(.headline)
                    if awardsLoaded {
                        Text("\(awards.count) options")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        ForEach(awards.prefix(40)) { award in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(award.name).font(.body)
                                Text([award.status, award.tier.isEmpty ? "" : "tier \(award.tier)"]
                                        .filter { !$0.isEmpty }
                                        .joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 3)
                        }
                        Text("Progress is recorded on the portal, not here — it writes into a promotion record, which is not something to change by accident.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.top, 4)
                    } else {
                        Button("Show awards and promotions") {
                            Task { await loadAwards() }
                        }
                        .buttonStyle(.bordered)
                        .disabled(busy)
                    }

                    Divider()
                }

                // --------------------------------------------------- support
                Text("Write to Jason").font(.headline)
                Picker("Kind", selection: $supportKind) {
                    ForEach(kinds, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)

                TextEditor(text: $supportText)
                    .frame(minHeight: 110)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.secondary.opacity(0.3))
                    )

                Button(busy ? "Sending…" : "Send") { Task { await send() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || supportText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(16)
        }
        .safeAreaInset(edge: .top) {
            HStack {
                Button("Back", action: onBack).font(.footnote)
                Spacer()
                if busy { ProgressView().scaleEffect(0.7) }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.bar)
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.pdf, .plainText, .data]) { result in
            guard case .success(let url) = result else { return }
            Task { await upload(url) }
        }
        .task { await loadShelves() }
    }

    private func loadShelves() async {
        do {
            shelves = try await api.shelves()
            if chosen.isEmpty { chosen = shelves.first?.path ?? "" }
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() }
        } catch {
            // Not surfaced: an empty shelf list already reads as "nothing set
            // up for you", which is what the person can act on.
        }
    }

    private func loadAwards() async {
        busy = true
        error = nil
        do {
            awards = try await api.awards()
            awardsLoaded = true
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    private func upload(_ url: URL) async {
        busy = true
        error = nil
        note = nil
        do {
            let result = try await api.upload(
                shelf: chosen, fileURL: url, filename: url.lastPathComponent)
            var parts: [String] = []
            if !result.saved.isEmpty { parts.append("Added \(result.saved.joined(separator: ", ")).") }
            if !result.skipped.isEmpty { parts.append("Skipped \(result.skipped.joined(separator: "; ")).") }
            if !result.note.isEmpty { parts.append(result.note) }
            note = parts.isEmpty ? "Done." : parts.joined(separator: " ")
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    private func send() async {
        busy = true
        error = nil
        note = nil
        do {
            note = try await api.support(kind: supportKind, text: supportText)
            supportText = ""
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }
}
