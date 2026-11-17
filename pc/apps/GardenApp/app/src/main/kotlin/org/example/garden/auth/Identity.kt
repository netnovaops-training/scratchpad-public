package org.example.garden.auth

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Who the signed-in person is, according to the server rather than the app. */
data class Identity(
    val email: String,
    val name: String,
) {
    /** What to greet them with: their name, or the email if no name is set. */
    val display: String get() = name.ifBlank { email }
}

/**
 * Asks the household provider who this access token belongs to.
 *
 * WHY THIS EXISTS AT ALL, when the app already knows it signed in
 * successfully. Because "the sign-in flow completed" and "the token I hold
 * is accepted by the backend" are different claims, and only the second one
 * means anything. A token can be well-formed, correctly stored, and still
 * rejected -- expired, revoked, issued by the wrong client. Calling a real
 * protected endpoint is the only way to know, and /oidc/userinfo is the one
 * endpoint guaranteed to exist on any OIDC provider.
 *
 * It is also the cheapest possible liveness check: no new backend route, no
 * proxy, no config change to a service the rest of the house depends on.
 *
 * ON THE 401. The provider answers an invalid token with 401 and the body
 * "Token is not valid." -- verified against the live server. That is the
 * signal to sign in again, NOT to retry: this provider issues no refresh
 * tokens, so nothing about waiting or retrying will make a stale token
 * work. See AuthManager.freshAccessToken for the same rule stated at the
 * other end.
 */
object IdentityService {

    private const val USERINFO = "${AuthConfig.ISSUER}/userinfo"

    suspend fun fetch(accessToken: String): Result<Identity> = withContext(Dispatchers.IO) {
        runCatching {
            val conn = (URL(USERINFO).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer $accessToken")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 15_000
                readTimeout = 15_000
            }

            try {
                val code = conn.responseCode
                if (code == 401) {
                    throw NotSignedInException()
                }
                if (code !in 200..299) {
                    val body = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    throw IllegalStateException("userinfo returned $code: ${body.take(120)}")
                }

                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                Identity(
                    email = json.optString("email"),
                    // `name` is what people.json carries; fall back through the
                    // other standard claims rather than showing an empty chip.
                    name = json.optString("name")
                        .ifBlank { json.optString("preferred_username") },
                )
            } finally {
                conn.disconnect()
            }
        }
    }
}

/**
 * The token is not accepted. Distinct from a network failure on purpose:
 * one of these means "send them back to sign-in", the other means "try
 * again in a moment", and conflating them produces an app that either
 * retries forever or logs people out because the wifi blipped.
 */
class NotSignedInException : Exception("Session expired. Sign in again.")
