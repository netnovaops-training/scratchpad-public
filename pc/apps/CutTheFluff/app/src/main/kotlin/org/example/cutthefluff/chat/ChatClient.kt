package org.example.cutthefluff.chat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.example.cutthefluff.auth.NotSignedInException
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Earlier turns sent with each question: enough context, bounded request size. */
private const val HISTORY_TURNS = 20

/**
 * Talks to Hermes -- the household assistant -- through the auth service.
 *
 * WHY NOT STRAIGHT TO A MODEL. The first version of this called
 * /auth/app/chat, which proxies to LiteLLM. That is a model and nothing
 * more: it has never met you, remembers nothing between turns, and can look
 * nothing up. Asking it "what is my name" got a shrug, which is the correct
 * answer from something with no memory and the wrong answer from something
 * calling itself your assistant.
 *
 * /auth/app/ask reaches Hermes instead, which has the per-person memory
 * store and the household tools. The route still exists for raw model access
 * when that is what is wanted.
 *
 * THE APP NEVER SAYS WHO IT IS. It sends its OIDC access token; the auth
 * service turns that into an identity server-side and hands it to Hermes.
 * Memory is per person, so a client able to name the person could read
 * anything anybody has told the assistant.
 *
 * NOT STREAMED, deliberately. Hermes answers in one piece after it has
 * finished using tools, and faking a stream by revealing a finished string
 * would add latency to pretend at liveness. The UI shows what it is doing
 * instead.
 */
class ChatClient(private val accessTokenProvider: suspend () -> Result<String>) {

    /** One reply from the assistant, plus whatever tools it used getting there. */
    data class Reply(
        val answer: String,
        val tools: List<String>,
        /**
         * Questions the garden catalogue is waiting on, if this turn raised
         * any. BUILT BY THE SERVER FROM THE DATABASE, never parsed out of
         * the answer text -- a button has to mean exactly one thing, and a
         * button the model wrote means whatever the model meant.
         */
        val approvals: List<Approval> = emptyList(),
    )

    /**
     * One waiting question, shaped for a Yes/No.
     *
     * `raw` is what a tag or a web page said; `suggested` is what this
     * catalogue would say instead. Both are shown, because approving the
     * second without seeing the first is approving a translation you were
     * not shown.
     */
    data class Approval(
        val id: Int,
        val field: String,
        val raw: String,
        val suggested: String,
        val confidence: Double,
        val source: String,
    )

    suspend fun ask(
        question: String,
        history: List<ChatMessage>,
        session: String = "android",
    ): Reply = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrElse { throw it }

        val payload = JSONObject().apply {
            put("text", question)
            put("session", session)
            // Prior turns travel with the request: the service is stateless,
            // so it can restart mid-conversation without losing the thread.
            put("history", JSONArray().apply {
                history.takeLast(HISTORY_TURNS).forEach {
                    put(JSONObject().apply {
                        put("role", it.role)
                        put("content", it.text)
                    })
                }
            })
        }

        val conn = (URL(ASK_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 20_000
            // Tool use is slow. A shelf search plus a model turn can run for
            // a couple of minutes, and cutting it off looks identical to
            // being ignored.
            readTimeout = 300_000
        }

        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }

            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(
                        err.ifBlank { "The assistant returned $code." }.take(200)
                    )
                }
            }

            val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            val tools = json.optJSONArray("tools")?.let { arr ->
                (0 until arr.length()).mapNotNull { arr.optString(it).takeIf(String::isNotBlank) }
            }.orEmpty()

            val approvals = json.optJSONArray("approvals")?.let { arr ->
                (0 until arr.length()).mapNotNull { i ->
                    arr.optJSONObject(i)?.let { o ->
                        Approval(
                            id = o.optInt("id"),
                            field = o.optString("field"),
                            raw = o.optString("raw"),
                            suggested = o.optString("suggested"),
                            confidence = o.optDouble("confidence", 0.0),
                            source = o.optString("source"),
                        )
                    }
                }.filter { it.id > 0 && it.suggested.isNotBlank() }
            }.orEmpty()

            Reply(
                answer = json.optString("answer").ifBlank { "(no answer came back)" },
                tools = tools,
                approvals = approvals,
            )
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Answer one of the catalogue's waiting questions.
     *
     * THE BODY IS AN ID AND A YES OR NO. It deliberately does not carry the
     * value: the server reads that from the row, so a tampered request
     * cannot approve a different word than the button showed. Identity
     * comes from the token for the same reason.
     */
    suspend fun decide(id: Int, approve: Boolean): String = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrElse { throw it }
        val conn = (URL(GARDEN_DECIDE_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 15_000
            readTimeout = 45_000
        }
        try {
            val body = JSONObject().apply {
                put("id", id)
                put("approve", approve)
            }
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(
                        err.ifBlank { "The garden returned $code." }.take(200)
                    )
                }
            }
            JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optString("status")
                .ifBlank { if (approve) "approved" else "rejected" }
        } finally {
            conn.disconnect()
        }
    }

    /**
     * The plain model, with no household context at all -- what a TRIAL gets.
     *
     * WHY A TRIAL CANNOT USE `ask` ABOVE. That route reaches Hermes, which
     * holds per-person memory and the household tools, and the server refuses
     * it to a trial token with a 403. This is the route that refusal points
     * at: /app/chat is an ordinary OpenAI-shaped proxy that has never met
     * anybody and can look nothing up.
     *
     * That difference is the honest shape of a trial rather than a limitation
     * to apologise for -- somebody evaluating whether they want an account is
     * not owed the contents of this house in the meantime.
     *
     * `onRemaining` is fed from the X-Trial-Remaining header the server sets,
     * so the count shown always comes from the side that owns it.
     */
    suspend fun plain(
        question: String,
        history: List<ChatMessage>,
        onRemaining: (Int) -> Unit = {},
    ): Reply = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrElse { throw it }

        val messages = JSONArray().apply {
            history.takeLast(HISTORY_TURNS).forEach {
                put(JSONObject().apply {
                    put("role", it.role)
                    put("content", it.text)
                })
            }
            put(JSONObject().apply {
                put("role", "user")
                put("content", question)
            })
        }
        val payload = JSONObject().apply {
            put("model", "chat")
            put("messages", messages)
            // Not streamed, matching `ask` above: one reply, one screen update.
            put("stream", false)
        }

        val conn = (URL(CHAT_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 20_000
            readTimeout = 120_000
        }

        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }

            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                // 402 is the trial running out, and the server's sentence
                // already names the address to write to. Its own type, because
                // the screen has to stop offering a text box rather than show
                // one more error above one.
                402 -> throw TrialFinishedException(
                    conn.errorStream?.bufferedReader()?.use { it.readText() }?.trim().orEmpty()
                )
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(
                        err.ifBlank { "The assistant returned $code." }.take(200)
                    )
                }
            }

            conn.getHeaderField("X-Trial-Remaining")?.toIntOrNull()?.let(onRemaining)

            val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            val answer = json.optJSONArray("choices")
                ?.optJSONObject(0)
                ?.optJSONObject("message")
                ?.optString("content")
                .orEmpty()

            Reply(answer = answer.ifBlank { "(no answer came back)" }, tools = emptyList())
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Things worth asking, chosen by the server for THIS person.
     *
     * ASKED FOR RATHER THAN BUILT IN, because the app cannot know what the
     * person can reach. Somebody with the garden should be offered the garden;
     * somebody on a trial should be offered only what a trial can actually
     * do -- and a suggestion the assistant then refuses is worse than no
     * suggestion, because it reads as the whole thing being broken.
     *
     * A failure is silent and returns nothing. The chips are a nicety; an
     * error message where they would have been is not.
     */
    suspend fun suggestions(): List<String> = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrNull() ?: return@withContext emptyList()
        val conn = (URL(PROMPTS_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 10_000
            readTimeout = 10_000
        }
        try {
            if (conn.responseCode !in 200..299) return@withContext emptyList()
            val arr = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optJSONArray("prompts") ?: return@withContext emptyList()
            (0 until arr.length()).map { arr.optString(it) }.filter { it.isNotBlank() }
        } catch (_: Exception) {
            emptyList()
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        private const val BASE = "https://example.org/auth/app"
        private const val PROMPTS_URL = "$BASE/prompts"
        private const val GARDEN_DECIDE_URL = "$BASE/garden/decide"
        private const val ASK_URL = "$BASE/ask"
        private const val CHAT_URL = "$BASE/chat"
    }
}

data class ChatMessage(val role: String, val text: String) {
    companion object {
        fun user(text: String) = ChatMessage("user", text)
        fun assistant(text: String) = ChatMessage("assistant", text)
    }
}

/**
 * The trial has no questions left. Carries the server's own sentence, which
 * names the address to write to -- reworded here it would only drift from
 * what the server says.
 */
class TrialFinishedException(serverSaid: String) : Exception(
    serverSaid.ifBlank {
        "That was the last of your free questions. Email user3@example.com to keep going."
    }
)
