package org.example.cutthefluff.auth

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.openid.appauth.AuthState
import net.openid.appauth.AuthorizationException
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationResponse
import net.openid.appauth.AuthorizationService
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.ResponseTypeValues
import net.openid.appauth.TokenRequest
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * The whole OIDC lifecycle: discover the provider, build a sign-in intent,
 * redeem the code AppAuth hands back, and keep the resulting AuthState
 * somewhere a rebooted phone can still find it.
 *
 * A SINGLE UNCONDITIONAL PKCE PATH, ON PURPOSE. AuthorizationRequest.Builder
 * generates a code_verifier/code_challenge pair unless told not to --
 * nothing here ever tells it not to. This app is registered as a PUBLIC
 * client on the server (see AuthConfig), where PKCE is not defence in depth,
 * it is the entire authentication. A build that skipped it would not fail
 * loudly; it would fail as a code the server refuses to redeem, which is a
 * worse failure mode to debug than a compile-time guarantee it can't happen.
 *
 * WHY EncryptedSharedPreferences AND NOT DataStore. See AuthConfig for what
 * this token represents -- the whole household SSO session, not a UI
 * preference. AppAuth ships no store of its own; this is the deliberate
 * choice of which one to give it.
 */
class AuthManager(private val context: Context) {

    private val prefs by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "cutthefluff_auth_state",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private var authService: AuthorizationService? = null

    /** The persisted session, or a fresh empty one on first launch. */
    fun currentState(): AuthState {
        val json = prefs.getString(KEY_STATE, null) ?: return AuthState()
        return AuthState.jsonDeserialize(json)
    }

    private fun persist(state: AuthState) {
        prefs.edit().putString(KEY_STATE, state.jsonSerializeString()).apply()
    }

    fun isSignedIn(): Boolean = currentState().isAuthorized

    fun signOut() {
        prefs.edit().remove(KEY_STATE).apply()
    }

    /**
     * Discovers the provider (fetches
     * ISSUER + "/.well-known/openid-configuration") and builds the intent
     * that starts the Custom Tab sign-in flow. AppAuth refuses WebView for
     * this outright (RFC 8252) -- there is no lower-friction fallback, and
     * none should be added.
     *
     * Returns the intent rather than launching it: AppAuth's own API needs
     * only a Context to build it (nothing here needs an Activity), and
     * launching is the call site's job via an ActivityResultLauncher, so
     * the result comes back through Compose state instead of a raw
     * onActivityResult override.
     */
    suspend fun buildSignInIntent(): Intent {
        val serviceConfig = fetchServiceConfig()
        val service = AuthorizationService(context).also { authService = it }

        val request = AuthorizationRequest.Builder(
            serviceConfig,
            AuthConfig.CLIENT_ID,
            ResponseTypeValues.CODE,
            AuthConfig.REDIRECT_URI,
        )
            .setScope(AuthConfig.SCOPE)
            // Deliberately NOT calling setCodeVerifier(null) or any
            // overload that would disable PKCE -- the builder's default
            // constructor already turns it on. This comment exists so a
            // future edit that "simplifies" this call has to delete an
            // explanation to introduce the bug.
            .build()

        return service.getAuthorizationRequestIntent(request)
    }

    private suspend fun fetchServiceConfig(): AuthorizationServiceConfiguration =
        suspendCoroutine { cont ->
            AuthorizationServiceConfiguration.fetchFromIssuer(Uri.parse(AuthConfig.ISSUER)) { config, ex ->
                when {
                    config != null -> cont.resume(config)
                    ex != null -> cont.resumeWithException(ex)
                    else -> cont.resumeWithException(
                        IllegalStateException("OIDC discovery returned neither a config nor an error")
                    )
                }
            }
        }

    /**
     * Called from the redirect activity's onNewIntent/onCreate with the
     * intent AppAuth's RedirectUriReceiverActivity forwards on. Exchanges
     * the code for tokens and persists the result either way -- a FAILED
     * exchange is still recorded, so `currentState().isAuthorized` reflects
     * reality rather than a stale "signed in" from a previous attempt.
     */
    suspend fun handleRedirect(intent: Intent): Result<AuthState> {
        val response = AuthorizationResponse.fromIntent(intent)
        val error = AuthorizationException.fromIntent(intent)
        val state = currentState().also { it.update(response, error) }

        if (response == null) {
            persist(state)
            return Result.failure(error ?: IllegalStateException("Sign-in was cancelled"))
        }

        val service = authService ?: AuthorizationService(context).also { authService = it }
        val tokenRequest = response.createTokenExchangeRequest()

        return suspendCoroutine { cont ->
            // The overload with NO ClientAuthentication argument -- not a
            // ClientAuthentication.NONE constant, which does not exist.
            // AppAuth's basic-auth/post-secret authenticators are only for
            // confidential clients; a public client sends client_id as a
            // plain token-request parameter and nothing else, which is what
            // omitting the argument here produces. See AuthConfig for why
            // there is no secret to pass in the first place.
            service.performTokenRequest(tokenRequest) { resp, ex ->
                state.update(resp, ex)
                persist(state)
                if (resp != null) {
                    cont.resume(Result.success(state))
                } else {
                    cont.resume(Result.failure(ex ?: IllegalStateException("Token exchange failed")))
                }
            }
        }
    }

    /**
     * Hands back a fresh access token -- when there is a valid one to hand
     * back. Wraps AuthState.performActionWithFreshTokens, AppAuth's own
     * integration point for this; call it before every authenticated API
     * request rather than reading a cached token.
     *
     * THE HOUSEHOLD PROVIDER ISSUES NO REFRESH TOKEN (rust/auth/src/oidc.rs
     * says so directly: "no refresh tokens"). So "refreshes if needed" is
     * only true for the lifetime of the access token itself -- once it
     * expires there is nothing on record to refresh it WITH, and this
     * returns failure rather than a token. THE CALLER MUST TREAT THAT
     * FAILURE AS "SIGN IN AGAIN", not as a network error to retry: retrying
     * a request that will never succeed without a fresh sign-in is exactly
     * the silent-failure mode Caddy's forward_auth behaviour compounds --
     * a protected endpoint hit with a stale token does not hand back a
     * clean 401, it hands back the auth gateway's HTML login page, which
     * looks like a malformed API response if this contract is not honoured
     * at the call site.
     */
    suspend fun freshAccessToken(): Result<String> {
        val service = authService ?: AuthorizationService(context).also { authService = it }
        val state = currentState()
        return suspendCoroutine { cont ->
            state.performActionWithFreshTokens(service) { accessToken, _, ex ->
                persist(state)
                if (accessToken != null) {
                    cont.resume(Result.success(accessToken))
                } else {
                    cont.resume(Result.failure(ex ?: IllegalStateException("No refresh token available")))
                }
            }
        }
    }

    fun dispose() {
        authService?.dispose()
        authService = null
    }

    companion object {
        private const val KEY_STATE = "auth_state"
    }
}
