import SwiftUI

/// Renders the assistant's Markdown instead of showing the asterisks.
///
/// WHY THIS EXISTS AT ALL. The model answers in Markdown whether or not
/// anybody asked it to, so a plain Text shows `### Ingredients` and `**240g**`
/// literally -- which is what the Android app did until it was fixed, and it
/// made every answer look like a bug.
///
/// SwiftUI's own `AttributedString(markdown:)` handles the INLINE marks --
/// bold, italic, code, links -- and nothing else: it has no concept of a
/// heading or a bullet, and given a multi-line string it will happily collapse
/// the lot into one paragraph. So the block structure is split here and each
/// line's inline marks are handed to it. That division is the whole design:
/// no hand-rolled bold parser, no hand-rolled block layout from Apple.
///
/// DELIBERATELY A SMALL SUBSET, matching the Android `Markdown.kt`: headings,
/// bullets, numbered lists, paragraphs. Tables and block quotes are not here
/// because the assistant does not produce them and a half-rendered table reads
/// worse than the raw text would.
struct MarkdownText: View {
    let raw: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(Self.blocks(raw).enumerated()), id: \.offset) { _, block in
                switch block {
                case .heading(let text, let level):
                    inline(text)
                        .font(level <= 1 ? .title3.weight(.semibold)
                              : level == 2 ? .headline
                              : .subheadline.weight(.semibold))
                        .padding(.top, 2)

                case .bullet(let text):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").foregroundStyle(.secondary)
                        inline(text)
                    }

                case .numbered(let number, let text):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(number).")
                            .foregroundStyle(.secondary)
                            // Numbers line up rather than wandering as they
                            // reach double digits.
                            .monospacedDigit()
                        inline(text)
                    }

                case .paragraph(let text):
                    inline(text)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// One line, with its inline marks applied.
    ///
    /// The `try?` falls back to the literal text. A malformed link or a stray
    /// bracket should cost the formatting of one line, never the answer.
    private func inline(_ text: String) -> Text {
        if let attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(attributed)
        }
        return Text(text)
    }

    enum Block {
        case heading(String, Int)
        case bullet(String)
        case numbered(Int, String)
        case paragraph(String)
    }

    /// Split into blocks. Line-based, because the assistant writes line-based
    /// Markdown and a full parser would be a great deal of code to handle
    /// constructs it never emits.
    static func blocks(_ raw: String) -> [Block] {
        var out: [Block] = []
        var paragraph: [String] = []

        func flush() {
            if !paragraph.isEmpty {
                out.append(.paragraph(paragraph.joined(separator: " ")))
                paragraph.removeAll()
            }
        }

        for line in raw.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                flush()
                continue
            }

            if trimmed.hasPrefix("#") {
                flush()
                let hashes = trimmed.prefix { $0 == "#" }.count
                let text = trimmed.dropFirst(hashes).trimmingCharacters(in: .whitespaces)
                out.append(.heading(text, hashes))
                continue
            }

            // "- x" and "* x", but NOT "*emphasis*" -- the marker has to be
            // followed by a space, or every italic line becomes a bullet.
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                flush()
                out.append(.bullet(String(trimmed.dropFirst(2))))
                continue
            }

            if let dot = trimmed.firstIndex(of: "."),
               let number = Int(trimmed[trimmed.startIndex..<dot]),
               trimmed.index(after: dot) < trimmed.endIndex,
               trimmed[trimmed.index(after: dot)] == " " {
                flush()
                let text = trimmed[trimmed.index(dot, offsetBy: 2)...]
                out.append(.numbered(number, String(text)))
                continue
            }

            paragraph.append(trimmed)
        }
        flush()
        return out
    }
}
