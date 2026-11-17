package org.example.garden.app

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.example.garden.auth.NotSignedInException
import org.json.JSONObject
import java.io.DataOutputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * The non-chat parts of the household API: shelves, uploads, career options,
 * and support mail.
 *
 * EVERY CALL IS AUTHENTICATED THE SAME WAY, with the OIDC access token, and
 * none of them says who the person is. The server derives that from the
 * token. This class could not name somebody else if it tried, which is the
 * property that makes an upload land on the right shelf and a support mail
 * come from the right person.
 */
class AppApi(private val accessTokenProvider: suspend () -> Result<String>) {

    data class Shelf(val path: String, val label: String, val sharedWith: List<String>)
    data class UploadResult(val saved: List<String>, val skipped: List<String>, val note: String)
    data class Award(val id: String, val name: String, val tier: String, val status: String)
    data class RecipeSummary(val id: String, val name: String, val course: String)
    data class Recipe(
        val id: String,
        val name: String,
        val course: String,
        val ingredients: List<String>,
        val steps: List<String>,
        /** "fallback" means the model was unreachable and the split is rough. */
        val parsedBy: String,
    )

    // ---------------------------------------------------------------- shelves

    suspend fun shelves(): List<Shelf> = withContext(Dispatchers.IO) {
        val json = getJson("$BASE/upload-targets")
        val arr = json.optJSONArray("targets") ?: return@withContext emptyList()
        (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let { o ->
                Shelf(
                    path = o.optString("path"),
                    label = o.optString("label"),
                    sharedWith = o.optJSONArray("shared_with")?.let { s ->
                        (0 until s.length()).map { s.optString(it) }
                    }.orEmpty(),
                )
            }
        }
    }

    // ----------------------------------------------------------------- upload

    /**
     * Streams the file rather than reading it into memory. A nursing textbook
     * runs to hundreds of megabytes, and loading one into a byte array to
     * upload it is how a phone runs out of heap holding a copy of something
     * it is only passing along.
     */
    suspend fun upload(context: Context, shelf: String, file: Uri): UploadResult =
        withContext(Dispatchers.IO) {
            val token = accessTokenProvider().getOrElse { throw it }
            val name = displayName(context, file)
            val boundary = "----Garden${System.currentTimeMillis()}"

            val conn = (URL("$BASE/upload").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                // Without this the whole body is buffered before sending, which
                // defeats the streaming above.
                setChunkedStreamingMode(64 * 1024)
                connectTimeout = 20_000
                readTimeout = 600_000
            }

            try {
                DataOutputStream(conn.outputStream).use { out ->
                    out.writeBytes("--$boundary\r\n")
                    out.writeBytes("Content-Disposition: form-data; name=\"target\"\r\n\r\n")
                    out.writeBytes("$shelf\r\n")

                    out.writeBytes("--$boundary\r\n")
                    out.writeBytes(
                        "Content-Disposition: form-data; name=\"files\"; filename=\"$name\"\r\n"
                    )
                    out.writeBytes("Content-Type: application/octet-stream\r\n\r\n")

                    context.contentResolver.openInputStream(file)?.use { input ->
                        input.copyTo(out, 64 * 1024)
                    } ?: throw IllegalStateException("Could not read that file.")

                    out.writeBytes("\r\n--$boundary--\r\n")
                }

                when (val code = conn.responseCode) {
                    401 -> throw NotSignedInException()
                    in 200..299 -> Unit
                    else -> {
                        val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                        throw IllegalStateException(
                            err.ifBlank { "Upload failed ($code)." }.take(240)
                        )
                    }
                }

                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                UploadResult(
                    saved = json.optJSONArray("saved")?.let { a ->
                        (0 until a.length()).map { a.optString(it) }
                    }.orEmpty(),
                    // The server explains WHY each file was refused; showing
                    // only a count would hide "arrived empty", which is the
                    // one a person can actually act on.
                    skipped = json.optJSONArray("skipped")?.let { a ->
                        (0 until a.length()).map { i ->
                            a.optJSONObject(i)?.let { o ->
                                "${o.optString("name")}: ${o.optString("why")}"
                            } ?: a.optString(i)
                        }
                    }.orEmpty(),
                    note = json.optString("note"),
                )
            } finally {
                conn.disconnect()
            }
        }

    private fun displayName(context: Context, uri: Uri): String {
        context.contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) {
                c.getString(idx)?.takeIf { it.isNotBlank() }?.let { return it }
            }
        }
        return uri.lastPathSegment?.substringAfterLast('/') ?: "upload"
    }

    // ----------------------------------------------------------------- awards

    suspend fun awards(): List<Award> = withContext(Dispatchers.IO) {
        val json = getJson("$BASE/awards")
        val arr = json.optJSONArray("awards") ?: return@withContext emptyList()
        (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let { o ->
                Award(
                    id = o.optString("id"),
                    name = o.optString("name").ifBlank { o.optString("id") },
                    tier = o.optString("tier"),
                    status = o.optString("status").ifBlank { o.optString("kind") },
                )
            }
        }
    }


    // ---------------------------------------------------------------- recipes

    suspend fun recipes(query: String = ""): List<RecipeSummary> = withContext(Dispatchers.IO) {
        val enc = java.net.URLEncoder.encode(query, "UTF-8")
        val json = getJson("$BASE/recipes?q=$enc")
        val arr = json.optJSONArray("recipes") ?: return@withContext emptyList()
        (0 until arr.length()).mapNotNull { i ->
            arr.optJSONObject(i)?.let { o ->
                RecipeSummary(
                    id = o.optString("id"),
                    name = o.optString("name"),
                    course = o.optString("course"),
                )
            }
        }
    }

    /**
     * One recipe, broken into steps. The FIRST person to open an unparsed
     * recipe pays for a model call -- several seconds -- and everybody after
     * them gets it from the server's cache, so the timeout here is generous
     * rather than optimistic.
     */
    suspend fun recipe(id: String): Recipe = withContext(Dispatchers.IO) {
        val json = getJson("$BASE/recipe/$id", readTimeoutMs = 300_000)
        fun list(name: String): List<String> =
            json.optJSONArray(name)?.let { a ->
                (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() }
            }.orEmpty()

        Recipe(
            id = json.optString("id", id),
            name = json.optString("name"),
            course = json.optString("course"),
            ingredients = list("ingredients"),
            steps = list("steps"),
            parsedBy = json.optString("parsed_by"),
        )
    }

    // ---------------------------------------------------------------- support

    suspend fun support(kind: String, text: String): String = withContext(Dispatchers.IO) {
        val token = accessTokenProvider().getOrElse { throw it }
        val payload = JSONObject().apply {
            put("kind", kind)
            put("text", text)
        }

        val conn = (URL("$BASE/support").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "application/json")
            connectTimeout = 20_000
            readTimeout = 60_000
        }

        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(err.ifBlank { "Could not send ($code)." }.take(240))
                }
            }
            JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optString("note")
                .ifBlank { "Sent." }
        } finally {
            conn.disconnect()
        }
    }

    // ------------------------------------------------------------------ shared

    private suspend fun getJson(url: String, readTimeoutMs: Int = 60_000): JSONObject {
        val token = accessTokenProvider().getOrElse { throw it }
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 20_000
            readTimeout = readTimeoutMs
        }
        try {
            when (val code = conn.responseCode) {
                401 -> throw NotSignedInException()
                in 200..299 -> Unit
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException(err.ifBlank { "Request failed ($code)." }.take(240))
                }
            }
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        private const val BASE = "https://example.org/auth/app"
    }
}
