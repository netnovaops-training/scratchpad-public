package org.example.cutthefluff.chat

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Renders the small slice of Markdown a chat model actually produces.
 *
 * WHY NOT A MARKDOWN LIBRARY. The models here emit headings, bold, numbered
 * steps, bullets, links and the occasional code span -- that is the whole
 * vocabulary. Pulling in a full CommonMark parser and an HTML bridge to
 * render six constructs would add a dependency, a WebView-shaped abstraction,
 * and a second set of typography rules that fights the app's own.
 *
 * WHY IT EXISTS AT ALL. Without it the bubble showed the SOURCE: literal
 * "### 1. To Cancel a Recurring Subscription" and "**Home**" with the
 * asterisks visible. That is not a cosmetic problem -- a numbered procedure
 * where every line starts with punctuation is genuinely harder to follow at
 * a glance, which is the one thing a set of steps has to be.
 *
 * SPACING IS THE POINT, more than the bold. The old rendering ran headings,
 * intros and steps together at one line height, so a wall of text arrived
 * and the eye had nowhere to land. Blocks are separated, headings get room
 * above them, and list items are indented and hang properly.
 */
@Composable
fun MarkdownText(
    text: String,
    modifier: Modifier = Modifier,
    color: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.onSurfaceVariant,
) {
    val blocks = remember(text) { parseBlocks(text) }

    Column(modifier = modifier) {
        blocks.forEachIndexed { i, block ->
            when (block) {
                is Block.Heading -> {
                    // Room ABOVE a heading, not below: it belongs to what
                    // follows, and a gap under it would orphan it.
                    if (i > 0) Spacer(Modifier.height(14.dp))
                    Text(
                        inline(block.text),
                        style = MaterialTheme.typography.titleSmall,
                        color = color,
                    )
                    Spacer(Modifier.height(4.dp))
                }

                is Block.Paragraph -> {
                    if (i > 0) Spacer(Modifier.height(8.dp))
                    Text(
                        inline(block.text),
                        style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                        color = color,
                    )
                }

                is Block.Item -> {
                    Spacer(Modifier.height(if (block.tight) 3.dp else 7.dp))
                    Row {
                        // Fixed-width marker so multi-line items hang under
                        // the text rather than under the number.
                        Text(
                            block.marker,
                            style = MaterialTheme.typography.bodyMedium.copy(
                                fontWeight = FontWeight.SemiBold,
                                lineHeight = 21.sp,
                            ),
                            color = color,
                            modifier = Modifier.width(if (block.marker.length > 2) 26.dp else 18.dp),
                        )
                        Text(
                            inline(block.text),
                            style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                            color = color,
                        )
                    }
                }
            }
        }
    }
}

private sealed interface Block {
    data class Heading(val text: String) : Block
    data class Paragraph(val text: String) : Block
    /** `tight` = the previous line was also an item, so they belong together. */
    data class Item(val marker: String, val text: String, val tight: Boolean) : Block
}

private val HEADING = Regex("""^\s{0,3}(#{1,6})\s+(.*)$""")
private val NUMBERED = Regex("""^\s{0,3}(\d{1,2})[.)]\s+(.*)$""")
private val BULLET = Regex("""^\s{0,3}[-*+]\s+(.*)$""")

private fun parseBlocks(src: String): List<Block> {
    val out = mutableListOf<Block>()
    var paragraph = StringBuilder()
    var lastWasItem = false

    fun flush() {
        if (paragraph.isNotBlank()) out += Block.Paragraph(paragraph.trim().toString())
        paragraph = StringBuilder()
    }

    for (raw in src.lines()) {
        val line = raw.trimEnd()

        when {
            line.isBlank() -> {
                flush()
                lastWasItem = false
            }

            HEADING.matches(line) -> {
                flush()
                out += Block.Heading(HEADING.find(line)!!.groupValues[2].trim())
                lastWasItem = false
            }

            NUMBERED.matches(line) -> {
                flush()
                val m = NUMBERED.find(line)!!
                out += Block.Item("${m.groupValues[1]}.", m.groupValues[2].trim(), lastWasItem)
                lastWasItem = true
            }

            BULLET.matches(line) -> {
                flush()
                out += Block.Item("•", BULLET.find(line)!!.groupValues[1].trim(), lastWasItem)
                lastWasItem = true
            }

            else -> {
                // A continuation line. Joined with a space rather than a
                // newline: the model hard-wraps prose, and preserving those
                // breaks would produce ragged text at a phone's width.
                if (paragraph.isNotEmpty()) paragraph.append(' ')
                paragraph.append(line.trim())
                lastWasItem = false
            }
        }
    }
    flush()
    return out
}

private val BOLD = Regex("""\*\*(.+?)\*\*|__(.+?)__""")
private val ITALIC = Regex("""(?<![*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![*\w])""")
private val CODE = Regex("""`([^`]+)`""")
private val LINK = Regex("""\[([^\]]+)]\(([^)]+)\)""")

/**
 * Inline formatting, applied in an order chosen so the passes cannot corrupt
 * one another: links first (their text may contain other markup), then code
 * (whose contents must stay literal), then bold before italic, since `**`
 * would otherwise be eaten as two italic markers.
 */
private fun inline(text: String): AnnotatedString {
    data class Mark(val range: IntRange, val style: SpanStyle, val replacement: String)

    var work = text
    val marks = mutableListOf<Mark>()

    // Links render as their label. The URL is dropped rather than shown --
    // a raw https:// in the middle of a sentence is exactly the noise this
    // is meant to remove, and it is not tappable here anyway.
    work = LINK.replace(work) { it.groupValues[1] }

    fun pass(re: Regex, style: SpanStyle) {
        while (true) {
            val m = re.find(work) ?: break
            val inner = m.groupValues.drop(1).firstOrNull { it.isNotEmpty() } ?: break
            val start = m.range.first
            work = work.replaceRange(m.range, inner)
            marks += Mark(start until (start + inner.length), style, inner)
        }
    }

    pass(CODE, SpanStyle(fontFamily = FontFamily.Monospace, fontSize = 13.sp))
    pass(BOLD, SpanStyle(fontWeight = FontWeight.SemiBold))
    pass(ITALIC, SpanStyle(fontStyle = androidx.compose.ui.text.font.FontStyle.Italic))

    return buildAnnotatedString {
        append(work)
        marks.forEach { m ->
            // Earlier passes shift later offsets, so a mark can end up out of
            // range. Skipping a style is a cosmetic loss; a crash mid-answer
            // is not.
            if (m.range.first >= 0 && m.range.last < work.length) {
                addStyle(m.style, m.range.first, m.range.last + 1)
            }
        }
    }
}

// Small local helper so the file does not need androidx.compose.runtime.remember
// imported at the top for a single use.
@Composable
private fun <T> remember(key: Any?, calc: () -> T): T =
    androidx.compose.runtime.remember(key) { calc() }
