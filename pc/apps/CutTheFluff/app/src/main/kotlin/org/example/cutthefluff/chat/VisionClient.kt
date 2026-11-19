package org.example.cutthefluff.chat

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.util.Base64
import androidx.exifinterface.media.ExifInterface
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.example.cutthefluff.auth.NotSignedInException
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

/** Earlier turns sent with a photo question (photos are big, so fewer than plain chat). */
private const val HISTORY_TURNS = 10

/** Longest server error text shown to the user. */
private const val MAX_ERROR_CHARS = 240

/**
 * Ask the assistant about a photo.
 *
 * THE PHOTO GOES TO /app/ask, NOT TO A VISION MODEL DIRECTLY. An earlier
 * version sent it straight to the vision job, which answered about the
 * picture and nothing else -- no memory, no shelf, no tools. Useful for "what
 * is this" and useless for "is this the part from the manual you indexed".
 *
 * Now the server does two stages: the vision model describes the image in
 * words, and Hermes answers using that description plus everything it
 * normally has. The app sends one request and stays out of the arrangement,
 * so when Hermes gains real image support nothing here changes.
 *
 * THE PHOTO IS SHRUNK AND STRAIGHTENED FIRST, because both failures are
 * silent. A modern camera produces 3-8MB, base64 inflates it by a third, and
 * an unresized upload on a phone signal times out somewhere in the middle
 * with no useful error. And phones store photos in sensor orientation with a
 * rotation flag -- hand the raw bytes over and the model describes a sideways
 * picture confidently and wrongly, which reads as the model being bad rather
 * than the caller sending it rotated.
 */
class VisionClient(private val accessTokenProvider: suspend () -> Result<String>) {

    /** The answer, plus what the vision model reported seeing. */
    data class Reply(
        val answer: String,
        val saw: String,
        val tools: List<String>,
        // Same field as ChatClient.Reply, and this is the path that
        // matters most: a photographed nursery tag is exactly when
        // there is something to confirm.
        val approvals: List<ChatClient.Approval> = emptyList(),
    )

    suspend fun ask(
        context: Context,
        image: Uri,
        question: String,
        history: List<ChatMessage> = emptyList(),
    ): Reply = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrElse { throw it }
        val jpeg = encode(context, image)

        val payload = JSONObject().apply {
            put("text", question.ifBlank { "What is this, and what should I know about it?" })
            put("image", "data:image/jpeg;base64,$jpeg")
            put("session", "android-photo")
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
            // Two model calls now, not one: describe, then answer. Both can
            // be slow on a 12GB card, and a model may need swapping in first.
            readTimeout = 600_000
        }

        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }

            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(
                        err.ifBlank { "The assistant returned $code." }.take(MAX_ERROR_CHARS)
                    )
                }
            }

            val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            val tools = json.optJSONArray("tools")?.let { arr ->
                (0 until arr.length()).mapNotNull { arr.optString(it).takeIf(String::isNotBlank) }
            }.orEmpty()

            Reply(
                answer = json.optString("answer").ifBlank { "(no answer came back)" },
                approvals = json.optJSONArray("approvals")?.let { arr ->
                    (0 until arr.length()).mapNotNull { i ->
                        arr.optJSONObject(i)?.let { o ->
                            ChatClient.Approval(
                                id = o.optInt("id"),
                                field = o.optString("field"),
                                raw = o.optString("raw"),
                                suggested = o.optString("suggested"),
                                confidence = o.optDouble("confidence", 0.0),
                                source = o.optString("source"),
                            )
                        }
                    }.filter { it.id > 0 && it.suggested.isNotBlank() }
                }.orEmpty(),
                // Shown to the person on request: when an answer is wrong
                // because the photo was misread, this is the only way to tell
                // a bad description from a bad assistant.
                saw = json.optString("saw"),
                tools = tools,
            )
        } finally {
            conn.disconnect()
        }
    }

    /** Downscale, straighten, and base64 a photo for sending. */
    private fun encode(context: Context, uri: Uri): String {
        val resolver = context.contentResolver

        // Measured first, decoded second: decoding a 12MP photo at full size
        // just to shrink it can exhaust the heap on a mid-range phone.
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }

        var sample = 1
        while (bounds.outWidth / sample > MAX_EDGE * 2 || bounds.outHeight / sample > MAX_EDGE * 2) {
            sample *= 2
        }

        val decoded = resolver.openInputStream(uri)?.use { stream ->
            BitmapFactory.decodeStream(
                stream, null, BitmapFactory.Options().apply { inSampleSize = sample }
            )
        } ?: throw IllegalStateException("Could not read that image.")

        val rotation = resolver.openInputStream(uri)?.use { stream ->
            when (ExifInterface(stream).getAttributeInt(
                ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL
            )) {
                ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                else -> 0f
            }
        } ?: 0f

        val scale = MAX_EDGE.toFloat() / maxOf(decoded.width, decoded.height).toFloat()
        val matrix = Matrix().apply {
            if (scale < 1f) postScale(scale, scale)
            if (rotation != 0f) postRotate(rotation)
        }
        val finished: Bitmap =
            if (scale < 1f || rotation != 0f) {
                Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
            } else {
                decoded
            }

        val out = ByteArrayOutputStream()
        finished.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (finished !== decoded) finished.recycle()
        decoded.recycle()

        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    companion object {
        private const val ASK_URL = "https://example.org/auth/app/ask"
        /** Plenty for reading a label or a screen; far below a camera's output. */
        private const val MAX_EDGE = 1280
        private const val JPEG_QUALITY = 85
    }
}
