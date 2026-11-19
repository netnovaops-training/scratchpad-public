package org.example.garden.garden

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Length of the session token the garden server sends; anything else is a broken reply. */
private const val TOKEN_HEX_CHARS = 64

/**
 * Logging in to the garden itself: Robin or Jason, with a password.
 *
 * HOW IT WORKS. The password is sent ONCE, to app_api.php?action=login, in
 * the body of a POST over HTTPS (never in the address, where it would end up
 * in logs). The site checks it and answers with a KEY: 64 random characters
 * that stand for "this is Jason" for 90 days. The app keeps only the key,
 * never the password, in encrypted storage, and sends it with every request
 * as `Authorization: Bearer <key>`. Log out tells the site to throw the key
 * away, so a lost phone can also be cut off by changing the password (which
 * throws away every key for that person).
 *
 * Guests never touch this file: browsing needs no key.
 *
 * Forgot password / first password: the site emails a link to the address
 * on file; see [forgotPasswordUrl].
 */
class GardenLogin(context: Context) {

    private val prefs by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "garden_garden_login",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    /** The name of whoever is logged in on this phone, or null. */
    fun savedName(): String? = prefs.getString(NAME, null)

    /** The key, for the `Authorization` header. Null for a guest. */
    fun token(): String? = prefs.getString(TOKEN, null)

    private fun forget() {
        prefs.edit().remove(TOKEN).remove(NAME).apply()
    }

    /**
     * Check a password with the site. On success the key is saved and the
     * proper name ("Jason") is returned; on failure, the site's own sentence
     * ("Invalid username or password", "Too many login attempts...").
     */
    suspend fun login(username: String, password: String): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val body = "username=" + URLEncoder.encode(username.trim(), "UTF-8") +
                "&password=" + URLEncoder.encode(password, "UTF-8")
            val conn = (URL("$API?action=login").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = 15_000
                readTimeout = 20_000
                setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
                setRequestProperty("Accept", "application/json")
            }
            try {
                conn.outputStream.use { it.write(body.toByteArray()) }
                val code = conn.responseCode
                val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                    ?.bufferedReader()?.use { it.readText() } ?: ""
                val json = runCatching { JSONObject(text) }.getOrNull()
                    ?: throw GardenException("The garden is not reachable right now.")
                if (!json.optBoolean("ok")) {
                    throw GardenException(json.optString("error").ifBlank { "Login failed." })
                }
                val name = json.optString("username")
                val token = json.optString("token")
                if (token.length != TOKEN_HEX_CHARS) throw GardenException("The garden sent back something unexpected.")
                prefs.edit().putString(TOKEN, token).putString(NAME, name).apply()
                name
            } finally {
                conn.disconnect()
            }
        }
    }

    /**
     * Is the saved key still good? True/false when the site answered; null
     * when it could not be reached (then the app carries on as the saved
     * name rather than logging somebody out over a weak signal).
     */
    suspend fun check(): Boolean? = withContext(Dispatchers.IO) {
        val token = token() ?: return@withContext false
        runCatching {
            val conn = (URL("$API?action=whoami").openConnection() as HttpURLConnection).apply {
                connectTimeout = 10_000
                readTimeout = 15_000
                setRequestProperty("Authorization", "Bearer $token")
            }
            try {
                when (conn.responseCode) {
                    in 200..299 -> true
                    401 -> { forget(); false }
                    else -> null
                }
            } finally {
                conn.disconnect()
            }
        }.getOrNull()
    }

    /** Log out: the site throws the key away, and so does the phone. */
    suspend fun logout() = withContext(Dispatchers.IO) {
        val token = token()
        forget()
        if (token != null) {
            runCatching {
                val conn = (URL("$API?action=logout").openConnection() as HttpURLConnection).apply {
                    connectTimeout = 10_000
                    readTimeout = 10_000
                    setRequestProperty("Authorization", "Bearer $token")
                }
                try { conn.responseCode } finally { conn.disconnect() }
            }
        }
    }

    companion object {
        private const val TOKEN = "token"
        private const val NAME = "name"
        private val API = org.example.garden.BuildConfig.GARDEN_BASE + "/app_api.php"

        /** Where "Forgot password? / First time?" sends people: the site's page. */
        val forgotPasswordUrl = org.example.garden.BuildConfig.GARDEN_BASE + "/login.php?forgot=1"
    }
}
