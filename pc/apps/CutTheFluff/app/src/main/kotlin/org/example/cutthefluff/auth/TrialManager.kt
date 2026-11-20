package org.example.cutthefluff.auth

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * A fixed number of questions, for somebody with no household account.
 *
 * WHAT THIS IS NOT. It is not a second way to sign in. The token it holds
 * opens exactly one route on the server -- the plain model proxy -- and every
 * other route refuses it. Nothing in this file should ever be handed to
 * AppApi: the shelf, recipes, uploads and the career screen all read household
 * data, and a trial is a stranger who typed an address into a phone.
 *
 * THE COUNTER IS THE SERVER'S, NOT THIS CLASS'S. What is cached here is only
 * ever the last number the server reported, so it can be shown without
 * spending a question to ask. Deciding locally whether a question is allowed
 * would put the limit inside the APK, where it is worth nothing -- see
 * AuthConfig for the same reasoning about the client secret.
 *
 * WHY EncryptedSharedPreferences: same store and same reasoning as
 * AuthManager. The trial token is a bearer credential, so it gets the same
 * treatment as the real one even though it is worth far less.
 */
class TrialManager(private val context: Context) {

    private val prefs by lazy {
        val key = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "cutthefluff-trial",
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    data class Trial(
        val email: String,
        val quota: Int,
        val remaining: Int,
        val contact: String,
    )

    fun token(): String? = prefs.getString(KEY_TOKEN, null)?.ifBlank { null }

    fun cached(): Trial? {
        val email = prefs.getString(KEY_EMAIL, null) ?: return null
        if (token() == null) return null
        return Trial(
            email = email,
            quota = prefs.getInt(KEY_QUOTA, 0),
            remaining = prefs.getInt(KEY_REMAINING, 0),
            contact = prefs.getString(KEY_CONTACT, DEFAULT_CONTACT) ?: DEFAULT_CONTACT,
        )
    }

    fun clear() = prefs.edit()
        .remove(KEY_TOKEN).remove(KEY_EMAIL)
        .remove(KEY_QUOTA).remove(KEY_REMAINING).remove(KEY_CONTACT)
        .apply()

    /** Note what the server last said was left, for display only. */
    fun noteRemaining(left: Int) {
        prefs.edit().putInt(KEY_REMAINING, left).apply()
    }

    /**
     * Redeem an invite code.
     *
     * The server returns the SAME trial for an address that already has one,
     * so reinstalling the app does not hand out a fresh eight. This method
     * therefore doubles as "get me back into the trial I already had".
     */
    suspend fun start(email: String, code: String): Result<Trial> = withContext(Dispatchers.IO) {
        runCatching {
            val conn = (URL(START).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 15_000
                readTimeout = 20_000
            }
            try {
                val payload = JSONObject()
                    .put("email", email.trim())
                    .put("code", code.trim())
                conn.outputStream.use { it.write(payload.toString().toByteArray()) }

                val status = conn.responseCode
                if (status !in 200..299) {
                    // The server's own sentence, shown verbatim. It is written
                    // for the person reading it -- "that code is not valid,
                    // check it with whoever sent you the app" -- and rewording
                    // it here would only make the app and the server disagree.
                    val why = conn.errorStream?.bufferedReader()?.use { it.readText() }
                        ?.trim().orEmpty()
                    throw IllegalStateException(
                        why.ifBlank { "Could not start the trial ($status)." }
                    )
                }

                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                val trial = Trial(
                    email = json.optString("email", email.trim()),
                    quota = json.optInt("quota", 0),
                    remaining = json.optInt("remaining", 0),
                    contact = json.optString("contact", DEFAULT_CONTACT),
                )
                prefs.edit()
                    .putString(KEY_TOKEN, json.getString("token"))
                    .putString(KEY_EMAIL, trial.email)
                    .putInt(KEY_QUOTA, trial.quota)
                    .putInt(KEY_REMAINING, trial.remaining)
                    .putString(KEY_CONTACT, trial.contact)
                    .apply()
                trial
            } finally {
                conn.disconnect()
            }
        }
    }

    /** Re-check with the server. Costs nothing against the quota. */
    suspend fun refresh(): Result<Trial> = withContext(Dispatchers.IO) {
        runCatching {
            val token = token() ?: throw IllegalStateException("No trial on this device.")
            val conn = (URL(STATUS).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 15_000
                readTimeout = 15_000
            }
            try {
                if (conn.responseCode == 404) {
                    // The trial is gone -- expired, or cleared on the server.
                    // Dropping the token locally is what puts the person back
                    // on the sign-in screen rather than into a loop of 401s.
                    clear()
                    throw IllegalStateException("That trial has ended.")
                }
                if (conn.responseCode !in 200..299) {
                    throw IllegalStateException("Could not check the trial.")
                }
                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                val trial = Trial(
                    email = json.optString("email"),
                    quota = json.optInt("quota", 0),
                    remaining = json.optInt("remaining", 0),
                    contact = json.optString("contact", DEFAULT_CONTACT),
                )
                noteRemaining(trial.remaining)
                trial
            } finally {
                conn.disconnect()
            }
        }
    }

    /**
     * The shape ChatClient wants. It asks for a token per request and does not
     * care which kind it is, which is the seam that lets a trial use the chat
     * screen without the chat screen knowing anything about trials.
     */
    fun tokenProvider(): suspend () -> Result<String> = {
        token()?.let { Result.success(it) }
            ?: Result.failure(NotSignedInException())
    }

    private companion object {
        // "/auth/app", not "/app". Caddy mounts the auth service under
        // /auth, so the route this file talks to is /auth/app/trial/start --
        // the same base ChatClient uses. Getting this wrong produces a 404
        // that looks exactly like a rejected code.
        const val BASE = "https://example.org/auth/app"
        const val START = "$BASE/trial/start"
        const val STATUS = "$BASE/trial/status"
        const val DEFAULT_CONTACT = "user3@example.com"

        const val KEY_TOKEN = "token"
        const val KEY_EMAIL = "email"
        const val KEY_QUOTA = "quota"
        const val KEY_REMAINING = "remaining"
        const val KEY_CONTACT = "contact"
    }
}
