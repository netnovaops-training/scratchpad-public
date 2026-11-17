import SwiftUI

/// The recipe box: search, pick one, cook from it.
///
/// BUILT TO BE COOKED FROM, not read. The steps are numbered, generously
/// spaced and tappable to strike through, because the person using this has
/// wet hands and is looking at a phone propped against something. That is the
/// same reasoning as the Android `RecipeScreen`.
struct RecipeView: View {
    let api: AppApi
    let onBack: () -> Void
    let onNeedsSignIn: () -> Void

    @State private var query = ""
    @State private var results: [AppApi.RecipeSummary] = []
    @State private var open: AppApi.Recipe?
    @State private var busy = false
    @State private var error: String?
    /// Which steps have been done. Index-based and deliberately not persisted:
    /// it is for one cook of one dish, and remembering it a week later would
    /// be wrong more often than useful.
    @State private var done: Set<Int> = []

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button(open == nil ? "Back" : "All recipes") {
                    if open == nil { onBack() } else { open = nil; done = [] }
                }
                .font(.footnote)
                Spacer()
                if busy { ProgressView().scaleEffect(0.7) }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 16)
            }

            if let recipe = open {
                detail(recipe)
            } else {
                list
            }
        }
        .task { await search() }
    }

    private var list: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Search recipes", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                    .onSubmit { Task { await search() } }
                Button("Find") { Task { await search() } }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)

            List(results) { summary in
                Button {
                    Task { await load(summary.id) }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(summary.name).font(.body)
                        if !summary.course.isEmpty {
                            Text(summary.course)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
            .listStyle(.plain)

            if results.isEmpty && !busy {
                Text(query.isEmpty ? "No recipes came back." : "Nothing matched that.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(24)
            }
        }
    }

    private func detail(_ recipe: AppApi.Recipe) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(recipe.name).font(.title3.weight(.semibold))
                if !recipe.course.isEmpty {
                    Text(recipe.course).font(.caption).foregroundStyle(.secondary)
                }

                if !recipe.ingredients.isEmpty {
                    Text("Ingredients").font(.headline)
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("•").foregroundStyle(.secondary)
                                Text(item)
                            }
                        }
                    }
                }

                if !recipe.steps.isEmpty {
                    Text("Method").font(.headline)
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(recipe.steps.enumerated()), id: \.offset) { index, step in
                            Button {
                                if done.contains(index) { done.remove(index) }
                                else { done.insert(index) }
                            } label: {
                                HStack(alignment: .firstTextBaseline, spacing: 10) {
                                    Text("\(index + 1)")
                                        .font(.footnote.weight(.bold))
                                        .monospacedDigit()
                                        .foregroundStyle(done.contains(index) ? .secondary : Color.accentColor)
                                        .frame(width: 22, alignment: .trailing)
                                    Text(step)
                                        .strikethrough(done.contains(index))
                                        .foregroundStyle(done.contains(index) ? .secondary : .primary)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                if recipe.parsedBy == "fallback" {
                    // Said plainly rather than hidden. The split into steps is
                    // usually done by a model; when it could not be reached
                    // the result is a rough guess, and somebody cooking from
                    // it should know that before they trust the order.
                    Text("The model was unreachable, so these steps were split roughly. Read them through before starting.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.top, 4)
                }
            }
            .padding(16)
        }
    }

    private func search() async {
        busy = true
        error = nil
        do {
            results = try await api.recipes(query: query)
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    private func load(_ id: String) async {
        busy = true
        error = nil
        done = []
        do {
            open = try await api.recipe(id: id)
        } catch let e as AuthError {
            if case .notSignedIn = e { onNeedsSignIn() } else { error = e.errorDescription }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }
}
