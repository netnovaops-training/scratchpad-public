package org.example.cutthefluff

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.example.cutthefluff.auth.AuthManager
import org.example.cutthefluff.auth.Identity
import org.example.cutthefluff.auth.IdentityService
import org.example.cutthefluff.auth.NotSignedInException
import org.example.cutthefluff.auth.TrialManager
import androidx.compose.material3.OutlinedTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import org.example.cutthefluff.chat.ChatClient
import org.example.cutthefluff.chat.ChatScreen
import org.example.cutthefluff.chat.VisionClient
import org.example.cutthefluff.ui.CutTheFluffTheme
import org.example.cutthefluff.ui.IntroScreen
import org.example.cutthefluff.app.AppApi
import org.example.cutthefluff.app.ToolsScreen
import org.example.cutthefluff.app.RecipeScreen
import org.example.cutthefluff.app.Updater

/**
 * Proves the chain end to end: sign in through the household OIDC provider,
 * then call a REAL protected endpoint with the resulting token and show who
 * the server says you are.
 *
 * The second half is the part that matters. "The sign-in sheet closed" is
 * not evidence the token works; a round trip to /oidc/userinfo is.
 */
class MainActivity : ComponentActivity() {

    private lateinit var authManager: AuthManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        authManager = AuthManager(applicationContext)
        // A share that LAUNCHED the app arrives here...
        Shared.offer(intent)

        setContent {
            // SHOWN ONCE PER LAUNCH. Held above the theme so nothing inside
            // can reset it; onCreate is the only thing that runs again, and
            // only on a genuine cold start.
            var showIntro by rememberSaveable { mutableStateOf(true) }
            CutTheFluffTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    // Explicit rather than inherited: Surface defaults to the
                    // `surface` colour, which is a shade lighter than the
                    // ground and would leave the app looking white again.
                    color = MaterialTheme.colorScheme.background,
                ) {
                    // The insignia, once per cold start. Placed here rather
                    // than inside the inset Box on purpose: it runs edge to
                    // edge, and only its Skip button needs to clear the
                    // clock -- which it does itself.
                    if (showIntro) {
                        IntroScreen(onDone = { showIntro = false })
                        return@Surface
                    }
                    // THE STATUS BAR IS NOT FREE SPACE.
                    //
                    // targetSdk 36 draws every app edge to edge and there is
                    // no opting out of it. Without this the top row of
                    // whatever screen is showing sits underneath the clock
                    // and the battery, which is exactly what it looked like:
                    // the buttons were on screen, drawn, and untappable,
                    // because the status bar was taking the touches.
                    //
                    // Padded here rather than in each screen so there is one
                    // place to be right, and INSIDE the Surface so the app's
                    // own colour still paints behind the status bar -- a grey
                    // strip across the top would be the other wrong answer.
                    //
                    // systemBars, not safeDrawing: safeDrawing folds in the
                    // keyboard, and ChatScreen and ToolsScreen already call
                    // imePadding() themselves. Using it here would push the
                    // message box up twice the height of the keyboard.
                    Box(
                        modifier = Modifier.windowInsetsPadding(
                            WindowInsets.systemBars
                        )
                    ) {
                        SignInScreen(authManager = authManager)
                    }
                }
            }
        }
    }

    /**
     * ...and a share into an ALREADY-RUNNING app arrives here instead.
     *
     * With launchMode="singleTask" the system reuses this activity rather
     * than creating a second one, so onCreate is not called again. Without
     * this override the first share of a session would work and every one
     * after it would silently do nothing -- the app would simply come to
     * the front with the old screen on it.
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        Shared.offer(intent)
    }

    override fun onDestroy() {
        authManager.dispose()
        super.onDestroy()
    }
}

private sealed interface Stage {
    data object SignedOut : Stage
    data object Working : Stage
    data class Verified(val who: Identity) : Stage
    data class Failed(val why: String, val needsSignIn: Boolean) : Stage
    /** Asking for an email address and an invite code. */
    data object TrialEntry : Stage
    data class OnTrial(val trial: TrialManager.Trial) : Stage
    /** The eight are gone. Carries the server's own sentence. */
    data class TrialOver(val said: String) : Stage
}

@Composable
private fun SignInScreen(authManager: AuthManager) {
    val scope = rememberCoroutineScope()
    val context = androidx.compose.ui.platform.LocalContext.current
    var stage by remember {
        mutableStateOf<Stage>(
            if (authManager.isSignedIn()) Stage.Working else Stage.SignedOut
        )
    }
    var showTools by remember { mutableStateOf(false) }
    var showRecipes by remember { mutableStateOf(false) }
    val trials = remember { TrialManager(context) }

    /** Ask the server who we are. The only real proof the token is good. */
    suspend fun verify() {
        stage = Stage.Working
        authManager.freshAccessToken().fold(
            onSuccess = { token ->
                IdentityService.fetch(token).fold(
                    onSuccess = { stage = Stage.Verified(it) },
                    onFailure = { e ->
                        stage = Stage.Failed(
                            why = e.message ?: "Could not reach the server",
                            // A rejected token needs a new sign-in; a dropped
                            // connection just needs another go.
                            needsSignIn = e is NotSignedInException,
                        )
                    },
                )
            },
            onFailure = { e ->
                // No refresh token exists on this provider, so a failure here
                // is always "sign in again", never "retry".
                stage = Stage.Failed(e.message ?: "Session expired", needsSignIn = true)
            },
        )
    }

    // If a session was restored from the encrypted store at launch, check it
    // against the server before claiming to be signed in.
    LaunchedEffect(Unit) {
        if (authManager.isSignedIn()) {
            verify()
        } else {
            // A REAL ACCOUNT ALWAYS WINS. This branch only runs when there is
            // no household session at all, so somebody who trials first and is
            // given an account later is never left stuck on the trial.
            trials.cached()?.let { cached ->
                stage = Stage.OnTrial(cached)
                // Then correct it from the server, because the cached number
                // is whatever this device last saw and the counter is not this
                // device's to keep.
                trials.refresh().onSuccess { stage = Stage.OnTrial(it) }
            }
        }
    }

    val redirectLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val data = result.data
        if (data == null) {
            stage = Stage.Failed("Sign-in cancelled", needsSignIn = true)
            return@rememberLauncherForActivityResult
        }
        scope.launch {
            authManager.handleRedirect(data).fold(
                onSuccess = { verify() },
                onFailure = { e ->
                    stage = Stage.Failed(e.message ?: "Sign-in failed", needsSignIn = true)
                },
            )
        }
    }

    fun startSignIn() {
        scope.launch {
            stage = Stage.Working
            runCatching { authManager.buildSignInIntent() }
                .onSuccess { redirectLauncher.launch(it) }
                .onFailure { e ->
                    stage = Stage.Failed(
                        e.message ?: "Could not reach sign-in", needsSignIn = true
                    )
                }
        }
    }

    // A TRIAL GETS THE SAME CHAT SCREEN, on purpose. Somebody deciding whether
    // they want an account should see the thing itself, not a stripped
    // preview of it. What differs is behind the screen: the questions go to
    // the plain model rather than to Hermes, and the household links are not
    // shown -- see ChatScreen's trialRemaining parameter.
    (stage as? Stage.OnTrial)?.let { onTrial ->
        ChatScreen(
            client = remember { ChatClient(trials.tokenProvider()) },
            vision = remember { VisionClient(trials.tokenProvider()) },
            // A trial must never be handed a real AppApi -- every route on it
            // reads household data. This one is wired to the trial token, so
            // the server refuses it, which is the belt to the UI's braces.
            api = remember { AppApi(trials.tokenProvider()) },
            updater = remember { Updater() },
            signedInAs = onTrial.trial.email,
            onSignOut = {
                // "Sign in" from a trial: keep the trial, go and get a real
                // account. Clearing it here would burn the remaining
                // questions of somebody who just wanted to look at the
                // sign-in screen.
                stage = Stage.SignedOut
            },
            onNeedsSignIn = {
                trials.clear()
                stage = Stage.Failed("That trial has ended.", needsSignIn = true)
            },
            onOpenTools = {},
            onOpenRecipes = {},
            trialRemaining = onTrial.trial.remaining,
            onTrialRemaining = { left ->
                trials.noteRemaining(left)
                stage = Stage.OnTrial(onTrial.trial.copy(remaining = left))
            },
            onTrialFinished = { said -> stage = Stage.TrialOver(said) },
        )
        return
    }

    // Once the token is proven good, the whole screen becomes the chat --
    // no header, no "you are signed in" panel taking up a third of a phone.
    // Verification was a gate, not a destination.
    (stage as? Stage.Verified)?.let { verified ->
        val api = remember { AppApi(authManager::freshAccessToken) }
        val signOut = {
            authManager.signOut()
            stage = Stage.SignedOut
        }
        val needsSignIn = {
            authManager.signOut()
            stage = Stage.Failed("Session expired.", needsSignIn = true)
        }

        if (showRecipes) {
            RecipeScreen(
                api = api,
                onBack = { showRecipes = false },
                onNeedsSignIn = needsSignIn,
            )
        } else if (showTools) {
            ToolsScreen(
                api = api,
                // The career section is hers. Shown by identity rather than by
                // a setting, so it cannot be switched on for the wrong person.
                isZee = verified.who.email.equals("user@example.com", ignoreCase = true),
                onBack = { showTools = false },
                onNeedsSignIn = needsSignIn,
            )
        } else {
            ChatScreen(
                client = remember { ChatClient(authManager::freshAccessToken) },
                vision = remember { VisionClient(authManager::freshAccessToken) },
                api = api,
                updater = remember { Updater() },
                signedInAs = verified.who.display,
                onSignOut = signOut,
                onNeedsSignIn = needsSignIn,
                onOpenTools = { showTools = true },
                onOpenRecipes = { showRecipes = true },
            )
        }
        return
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Cut The Fluff", style = MaterialTheme.typography.headlineSmall)
        // The running build, on screen. Without this, "it still looks the
        // same" and "the new APK did not install" are indistinguishable --
        // which is exactly the hour that got lost to a cached download.
        Text(
            "v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
            style = MaterialTheme.typography.labelSmall,
        )
        Spacer(Modifier.height(12.dp))

        when (val s = stage) {
            is Stage.SignedOut -> {
                Text(
                    "Sign in with your household account.",
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                Button(onClick = { startSignIn() }) { Text("Sign in") }
                Spacer(Modifier.height(10.dp))
                OutlinedButton(onClick = { stage = Stage.TrialEntry }) {
                    Text("Try it with a code")
                }
            }

            is Stage.TrialEntry -> {
                var email by remember { mutableStateOf("") }
                var code by remember { mutableStateOf("") }
                var busy by remember { mutableStateOf(false) }
                var why by remember { mutableStateOf<String?>(null) }

                Text(
                    "Enter the code you were sent, and an email address to reach you on.",
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                OutlinedTextField(
                    value = email,
                    onValueChange = { email = it; why = null },
                    label = { Text("Email") },
                    singleLine = true,
                    // The email keyboard, and no autocapitalisation: a phone
                    // helpfully capitalising the first letter of an address is
                    // a support question waiting to happen.
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Email,
                        capitalization = KeyboardCapitalization.None,
                    ),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it; why = null },
                    label = { Text("Invite code") },
                    singleLine = true,
                    // Codes are printed in capitals, and the server normalises
                    // anyway -- so this matches what they were sent rather
                    // than relying on that.
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Characters,
                    ),
                )
                why?.let {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        textAlign = TextAlign.Center,
                    )
                }
                Spacer(Modifier.height(16.dp))
                Button(
                    enabled = !busy && email.isNotBlank() && code.isNotBlank(),
                    onClick = {
                        busy = true
                        why = null
                        scope.launch {
                            trials.start(email, code).fold(
                                onSuccess = { stage = Stage.OnTrial(it) },
                                onFailure = { e ->
                                    busy = false
                                    why = e.message ?: "Could not start the trial."
                                },
                            )
                        }
                    },
                ) { Text(if (busy) "Checking…" else "Start") }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { stage = Stage.SignedOut }) { Text("Back") }
            }

            is Stage.TrialOver -> {
                Text(
                    s.said,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                // No "try again" button, deliberately. There is nothing to
                // retry: the next thing that helps is an email, and offering a
                // button that cannot work would only send somebody round the
                // same refusal a second time.
                Button(onClick = { stage = Stage.SignedOut }) { Text("Back to sign in") }
            }

            is Stage.OnTrial -> Unit   // handled above, outside this Column

            is Stage.Working -> {
                CircularProgressIndicator()
                Spacer(Modifier.height(12.dp))
                Text("Checking with the server…", style = MaterialTheme.typography.bodySmall)
            }

            is Stage.Verified -> Unit   // handled above, outside this Column

            is Stage.Failed -> {
                Text(
                    s.why,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                if (s.needsSignIn) {
                    Button(onClick = {
                        authManager.signOut()
                        startSignIn()
                    }) { Text("Sign in again") }
                } else {
                    Button(onClick = { scope.launch { verify() } }) { Text("Retry") }
                }
            }
        }
    }
}
