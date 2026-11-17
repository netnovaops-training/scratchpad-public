package org.example.garden.chat

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * The conversation survives leaving the app.
 *
 * JASON, 2026-09-27: "Can we resume the chat after we closed or move away
 * from a chat so that the session resumes even after I move away from the
 * app for a brief moment."
 *
 * WHY IT WAS LOST. The history lived in `remember { mutableStateListOf() }`,
 * which is memory belonging to a composable. Android reclaims that the
 * moment the app is backgrounded under pressure -- and a phone that is
 * taking photographs of plant tags is exactly a phone under memory
 * pressure. Answering the door mid-conversation lost the conversation.
 *
 * WHY A FILE AND NOT rememberSaveable. Saved instance state rides in a
 * Binder transaction with a hard limit around 1 MB, shared with every other
 * piece of saved state in the process. A chat that has been going a while,
 * with a model that answers in paragraphs, walks straight into
 * TransactionTooLargeException -- and it does not fail at the moment the
 * history gets long, it fails later, on rotation, in a way that looks
 * unrelated. A file has none of that and survives the process being killed
 * outright, which saved instance state does not reliably do.
 *
 * WHAT IS DELIBERATELY NOT KEPT. Photographs. The history holds what was
 * SAID about an image, never the image, because a conversation full of
 * plant photographs would grow without limit on a phone and this file would
 * become the largest thing the app owns.
 *
 * NEVER THROWS. A chat that cannot be restored should start empty, not
 * crash. Every failure here is logged and swallowed: losing the history is
 * a disappointment, and refusing to open the app because of it is not a
 * trade anybody would choose.
 */
object ChatStore {

    private const val FILE = "chat-history.json"
    private const val TAG = "ChatStore"

    /**
     * How much is kept. Long enough that yesterday's conversation is still
     * there, short enough that the file stays small and reading it is
     * instant. The oldest messages go first, because the recent ones are
     * what the next question follows on from.
     */
    private const val KEEP = 80

    /**
     * A single message is capped too. The model occasionally returns
     * something enormous, and one runaway answer should not make every
     * future launch slower forever.
     */
    private const val MAX_CHARS = 8_000

    private fun file(context: Context) = File(context.filesDir, FILE)

    suspend fun load(context: Context): List<ChatMessage> =
        withContext(Dispatchers.IO) {
            val f = file(context)
            if (!f.exists()) return@withContext emptyList()
            try {
                val arr = JSONArray(f.readText())
                val out = ArrayList<ChatMessage>(arr.length())
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    val role = o.optString("role")
                    val text = o.optString("text")
                    // A message with no role is not a message. Skipping it
                    // beats rendering a blank bubble nobody can explain.
                    if (role.isNotBlank() && text.isNotBlank()) {
                        out.add(ChatMessage(role, text))
                    }
                }
                out
            } catch (e: Exception) {
                // A half-written or hand-edited file. Start clean rather
                // than fight it, and say so in the log so it is findable.
                Log.w(TAG, "could not read the saved conversation", e)
                runCatching { f.delete() }
                emptyList()
            }
        }

    suspend fun save(context: Context, history: List<ChatMessage>) =
        withContext(Dispatchers.IO) {
            try {
                val recent = history.takeLast(KEEP)
                val arr = JSONArray()
                for (m in recent) {
                    arr.put(
                        JSONObject()
                            .put("role", m.role)
                            .put("text", m.text.take(MAX_CHARS))
                    )
                }
                // WRITTEN ASIDE AND MOVED INTO PLACE. Writing over the real
                // file means a phone killed mid-write leaves a truncated
                // one, and the next launch throws away a conversation that
                // was almost entirely intact. The rename is atomic, so the
                // file is either the old history or the new one.
                val tmp = File(context.filesDir, "$FILE.tmp")
                tmp.writeText(arr.toString())
                if (!tmp.renameTo(file(context))) {
                    file(context).writeText(arr.toString())
                    tmp.delete()
                }
            } catch (e: Exception) {
                Log.w(TAG, "could not save the conversation", e)
            }
        }

    /** Used by "start a new conversation". */
    suspend fun clear(context: Context) = withContext(Dispatchers.IO) {
        runCatching { file(context).delete() }
        Unit
    }

    // THE UNSENT MESSAGE IS WORTH KEEPING TOO, and for a sharper reason
    // than the history. A send that fails -- the phone could not resolve
    // the host, the network dropped between rooms -- currently loses what
    // was typed, which is the one moment somebody is certain to be
    // irritated. It is small, so it lives in preferences rather than a file.
    private const val PREFS = "chat"
    private const val KEY_DRAFT = "draft"

    fun loadDraft(context: Context): String =
        runCatching {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY_DRAFT, "") ?: ""
        }.getOrDefault("")

    fun saveDraft(context: Context, draft: String) {
        runCatching {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_DRAFT, draft.take(MAX_CHARS)).apply()
        }
    }
}
