package org.example.cutthefluff.chat

import android.content.Context
import android.net.Uri
import androidx.core.content.FileProvider
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.PickVisualMediaRequest
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedButton
import android.content.Intent
import androidx.compose.ui.platform.LocalContext
import java.io.File
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch
import org.example.cutthefluff.Shared
import org.example.cutthefluff.auth.NotSignedInException
import org.example.cutthefluff.chat.TrialFinishedException

/**
 * The chat. Deliberately plain: a list, a box, a button.
 *
 * ONE DESIGN NOTE WORTH KEEPING. The streaming reply is held in a single
 * mutable string that the last list item reads, rather than appending a new
 * message per token. Appending per token would push a new item into the
 * LazyColumn hundreds of times for one answer, which is what makes a
 * streaming chat scroll jankily and burn battery.
 */
@Composable
fun ChatScreen(
    client: ChatClient,
    vision: VisionClient,
    api: org.example.cutthefluff.app.AppApi,
    updater: org.example.cutthefluff.app.Updater,
    signedInAs: String,
    onSignOut: () -> Unit,
    onNeedsSignIn: () -> Unit,
    onOpenTools: () -> Unit,
    onOpenRecipes: () -> Unit,
    /**
     * Non-null when this is a TRIAL rather than a household account: the
     * number of questions left, as the server last reported it.
     *
     * It drives two things, and both matter. The questions go to the plain
     * model instead of Hermes, because the server refuses Hermes to a trial
     * token -- so without this the screen would send every question straight
     * into a 403. And the Recipes and Tools links are hidden, because they
     * read household data and a trial is refused all of it; offering a person
     * a door that always slams is worse than not showing the door.
     */
    trialRemaining: Int? = null,
    onTrialRemaining: (Int) -> Unit = {},
    onTrialFinished: (String) -> Unit = {},
) {
    val onTrial = trialRemaining != null
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val context = LocalContext.current

    // The photo waiting to be asked about. Held rather than sent immediately,
    // so the question and the picture arrive together -- "what is this?" with
    // no question attached is a worse prompt than the person would have
    // written given the chance.
    var pending by remember { mutableStateOf<Uri?>(null) }

    val history = remember { mutableStateListOf<ChatMessage>() }
    // Things worth asking, chosen BY THE SERVER for this person -- see
    // ChatClient.suggestions. Empty until they arrive and empty forever if the
    // call fails, which is the right failure for a nicety.
    var suggestions by remember { mutableStateOf<List<String>>(emptyList()) }
    var draft by remember { mutableStateOf("") }
    var lastTools by remember { mutableStateOf<List<String>>(emptyList()) }
    // QUESTIONS THE CATALOGUE IS WAITING ON, from the last answer. Held
    // separately from `history` because they are not part of the
    // conversation -- they are a thing to do about it, and they disappear
    // once done rather than scrolling away as a transcript would.
    val waiting = remember { mutableStateListOf<ChatClient.Approval>() }
    var deciding by remember { mutableStateOf<Int?>(null) }
    // What the vision model reported seeing. Kept so a wrong answer
    // about a photo can be traced to a bad description rather than
    // blamed on the assistant.
    var lastSaw by remember { mutableStateOf("") }
    var showSaw by remember { mutableStateOf(false) }

    // The update offer. Checked once per launch against a few-hundred-byte
    // manifest, so the 40MB APK is only fetched when there is genuinely a
    // newer one.
    var update by remember {
        mutableStateOf<org.example.cutthefluff.app.Updater.Available?>(null)
    }
    var updateProgress by remember { mutableStateOf(-1) }

    // The shelf this person's uploads go to. Resolved once so the Doc button
    // is one tap rather than a picker every time; the Tools screen is where
    // somebody chooses a different one.
    var myShelf by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // Where the camera app was told to write. Kept because the capture
    // contract reports only success or failure, not the location.
    var photoTarget by remember { mutableStateOf<Uri?>(null) }
    val cameraLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { ok ->
        // false means the person backed out, which is not an error worth
        // showing them.
        pending = if (ok) photoTarget else null
    }

    // PickVisualMedia, NOT ACTION_GET_CONTENT or READ_MEDIA_IMAGES. The
    // system photo picker runs in its own process and hands back only the
    // one image chosen, so it needs NO permission at all -- the app never
    // gains the ability to read the gallery, only to receive a picture from
    // it. Asking for gallery access to import one photo would be asking for
    // far more than the feature needs.
    val galleryLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        // null means they closed the picker without choosing.
        if (uri != null) pending = uri
    }

    var photoMenu by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        update = updater.check()
        suggestions = client.suggestions()
        // Not asked for on a trial: the server refuses it, and a request whose
        // only possible answer is 403 is a round trip to learn what is already
        // known. Leaving myShelf null is also what correctly hides the
        // document-upload button, which a trial could not use either.
        if (!onTrial) {
            runCatching { api.shelves() }
                .onSuccess { myShelf = it.firstOrNull()?.path }
        }
    }

    // Documents rather than photos: ACTION_GET_CONTENT sees a PDF in
    // Downloads, which the media picker does not.
    val pickDoc = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        val shelf = myShelf
        if (uri == null || shelf == null) return@rememberLauncherForActivityResult
        busy = true
        error = ""
        scope.launch {
            runCatching { api.upload(context, shelf, uri) }
                .onSuccess { r ->
                    history += ChatMessage.assistant(
                        buildString {
                            if (r.saved.isNotEmpty()) {
                                append("Added ")
                                append(r.saved.joinToString(", "))
                                append(". ")
                            }
                            if (r.skipped.isNotEmpty()) {
                                append("Skipped: ")
                                append(r.skipped.joinToString("; "))
                                append(". ")
                            }
                            append(r.note)
                        }
                    )
                }
                .onFailure { e ->
                    if (e is NotSignedInException) onNeedsSignIn()
                    else error = e.message ?: "Upload failed."
                }
            busy = false
        }
    }

    // Keep the newest message on screen.
    LaunchedEffect(history.size, busy) {
        val last = history.lastIndex.coerceAtLeast(0)
        if (history.isNotEmpty()) listState.animateScrollToItem(last)
    }

    /** A file the camera app may write into, and a content:// URI for it. */
    fun newPhotoTarget(ctx: Context): Uri {
        val dir = File(ctx.cacheDir, "images").apply { mkdirs() }
        val file = File(dir, "shot_${System.currentTimeMillis()}.jpg")
        return FileProvider.getUriForFile(ctx, "${ctx.packageName}.fileprovider", file)
    }

    fun send() {
        val text = draft.trim()
        val photo = pending
        if (photo != null) {
            if (busy) return
            history += ChatMessage.user(
                if (text.isEmpty()) "[photo] What is this?" else "[photo] $text"
            )
            draft = ""
            pending = null
            error = null
            busy = true
            lastTools = emptyList()
            lastSaw = ""
            showSaw = false
            scope.launch {
                runCatching { vision.ask(context, photo, text, history.dropLast(1)) }
                    .onSuccess { reply ->
                        history += ChatMessage.assistant(reply.answer)
                        lastTools = reply.tools
                        waiting.clear()
                        waiting.addAll(reply.approvals)
                        lastSaw = reply.saw
                    }
                    .onFailure { e ->
                        if (e is NotSignedInException) onNeedsSignIn()
                        else error = e.message?.takeIf { m -> m.isNotBlank() }
                            ?: "${e.javaClass.simpleName} (no detail)"
                    }
                busy = false
            }
            return
        }
        if (text.isEmpty() || busy) return

        history += ChatMessage.user(text)
        draft = ""
        error = null
        busy = true
        lastTools = emptyList()

        scope.launch {
            // history already contains this turn's question, so the prior
            // turns are everything before it.
            val prior = history.dropLast(1)
            runCatching {
                if (onTrial) client.plain(text, prior, onRemaining = onTrialRemaining)
                else client.ask(text, prior)
            }
                .onSuccess { reply ->
                    history += ChatMessage.assistant(reply.answer)
                    lastTools = reply.tools
                    // Replaced, not appended: these are what is waiting NOW.
                    // Accumulating them would leave a button for a question
                    // somebody already answered somewhere else.
                    waiting.clear()
                    waiting.addAll(reply.approvals)
                }
                .onFailure { e ->
                    if (e is NotSignedInException) {
                        onNeedsSignIn()
                    } else if (e is TrialFinishedException) {
                        // Handed upwards rather than shown here: the trial is
                        // over, so the answer is a screen with the address to
                        // write to on it, not an error above a text box that
                        // will never work again.
                        onTrialFinished(e.message.orEmpty())
                    } else {
                        // Show the real reason. The generic fallback is only
                        // for exceptions that genuinely carry no message --
                        // and one of those (AppAuth's, whose getMessage() is
                        // null) is exactly what made a five-minute token
                        // failure look like an unexplained shrug.
                        error = e.message?.takeIf { it.isNotBlank() }
                            ?: "${e.javaClass.simpleName} (no detail)"
                    }
                }
            busy = false
        }
    }

    // A LINK SHARED FROM ANOTHER APP, put in the box and sent.
    //
    // KEYED ON THE PENDING VALUE so it fires when something arrives rather
    // than only once at first composition -- a second share into an app
    // that is already open has to work too.
    //
    // IT WAITS FOR `busy`. Sending while a previous question is still in
    // flight would drop the first one's answer on the floor; the share is
    // taken on the next pass instead.
    LaunchedEffect(Shared.pending, busy) {
        if (!busy && Shared.pending != null) {
            Shared.take()?.let { shared ->
                draft = shared
                send()
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .imePadding()
            .navigationBarsPadding(),
    ) {
        update?.let { up ->
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.primaryContainer)
                    .padding(horizontal = 16.dp, vertical = 10.dp),
            ) {
                Text(
                    "Version ${up.versionName} is available",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
                if (up.notes.isNotBlank()) {
                    Text(
                        up.notes,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                    )
                }
                Spacer(Modifier.height(6.dp))
                if (updateProgress in 0..99) {
                    Text(
                        "Downloading ${updateProgress}%",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                    )
                } else {
                    Row {
                        Button(onClick = {
                            updateProgress = 0
                            scope.launch {
                                runCatching {
                                    updater.downloadAndInstall(context, up) { p ->
                                        updateProgress = p
                                    }
                                }.onFailure { e ->
                                    updateProgress = -1
                                    error = e.message ?: "The update could not be installed."
                                }
                            }
                        }) { Text("Update") }
                        Spacer(Modifier.padding(4.dp))
                        OutlinedButton(onClick = { update = null }) { Text("Later") }
                    }
                }
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(signedInAs, style = MaterialTheme.typography.labelMedium)
            Row {
                if (onTrial) {
                    Text(
                        if (trialRemaining == 1) "1 question left"
                        else "$trialRemaining questions left",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(4.dp),
                    )
                    Spacer(Modifier.padding(6.dp))
                    Text(
                        "Sign in",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.padding(4.dp).clickableNoRipple(onSignOut),
                    )
                } else {
                Text(
                    "Recipes",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(4.dp).clickableNoRipple(onOpenRecipes),
                )
                Spacer(Modifier.padding(4.dp))
                Text(
                    "Tools",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(4.dp).clickableNoRipple(onOpenTools),
                )
                Spacer(Modifier.padding(4.dp))
                // CHANGING A PASSWORD HAS TO BE REACHABLE FROM IN HERE.
                //
                // Jason, 2026-09-25: "I have other family members that are
                // using my apps that I wouldn't be able to have them reset
                // it within the app if they're using it."
                //
                // Forgetting a password was already covered -- the sign-in
                // page the app opens carries "Forgotten your password?".
                // CHANGING one you still know was not: /auth/change exists
                // and nothing linked to it, so it may as well not have.
                //
                // Opened in the phone's browser rather than a web view on
                // purpose. The page needs the household session cookie, the
                // browser is where that cookie lives, and a password form
                // belongs somewhere the person can see the padlock and the
                // address -- not inside an app frame where any app could
                // draw the same thing.
                Text(
                    "Password",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(4.dp).clickableNoRipple {
                        runCatching {
                            context.startActivity(
                                Intent(
                                    Intent.ACTION_VIEW,
                                    Uri.parse(CHANGE_PASSWORD_URL),
                                )
                            )
                        }
                    },
                )
                Spacer(Modifier.padding(6.dp))
                Text(
                    "Sign out",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(4.dp).clickableNoRipple(onSignOut),
                )
                }
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // ONLY ON AN EMPTY CONVERSATION. Suggestions are for the moment
            // somebody opens the app and does not know what to type; once
            // there is a thread, they are clutter above it.
            if (history.isEmpty() && suggestions.isNotEmpty()) {
                item {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            "Try asking",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        suggestions.take(4).forEach { suggestion ->
                            Surface(
                                shape = MaterialTheme.shapes.large,
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    // Tapping SENDS it rather than filling the
                                    // box: the point is one tap to a real
                                    // answer. Somebody who wants to edit it
                                    // first can type their own.
                                    .clickableNoRipple {
                                        // Put it in the box and send it the
                                        // ordinary way, rather than teaching
                                        // send() a second entry point: the
                                        // text then takes exactly the path a
                                        // typed question takes.
                                        draft = suggestion
                                        send()
                                    },
                            ) {
                                Text(
                                    suggestion,
                                    style = MaterialTheme.typography.bodyMedium,
                                    modifier = Modifier.padding(
                                        horizontal = 14.dp, vertical = 11.dp),
                                )
                            }
                        }
                    }
                }
            }

            items(history) { m -> Bubble(m.text, mine = m.role == "user") }

            // AFTER THE ANSWER, NOT INSIDE IT. The answer is the research;
            // these are the questions the research raised, and mixing them
            // would make the reply look like it was asking for permission.
            if (waiting.isNotEmpty() && !busy) {
                item {
                    Text(
                        if (waiting.size == 1) "One thing to confirm"
                        else "${waiting.size} things to confirm",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 4.dp, top = 4.dp),
                    )
                }
            }
            items(waiting, key = { it.id }) { a ->
                ApprovalCard(
                    approval = a,
                    busy = deciding == a.id,
                    onDecide = { yes ->
                        deciding = a.id
                        scope.launch {
                            runCatching { client.decide(a.id, yes) }
                                .onSuccess {
                                    waiting.remove(a)
                                    history += ChatMessage.assistant(
                                        if (yes)
                                            "Noted — \"${a.raw}\" means " +
                                                "\"${a.suggested}\" in this catalogue from " +
                                                "now on."
                                        else
                                            "Left alone. Nothing was written, and " +
                                                "I'll ask again next time that wording " +
                                                "turns up."
                                    )
                                }
                                .onFailure { e ->
                                    if (e is NotSignedInException) onNeedsSignIn()
                                    else error = e.message?.takeIf { it.isNotBlank() }
                                        ?: "That did not go through."
                                }
                            deciding = null
                        }
                    },
                )
            }

            if (busy) {
                item {
                    Text(
                        "Thinking…",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(8.dp),
                    )
                }
            } else if (lastSaw.isNotBlank()) {
                item {
                    Column(modifier = Modifier.padding(horizontal = 8.dp)) {
                        Text(
                            if (showSaw) "hide what it saw" else "what did it see?",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.clickableNoRipple { showSaw = !showSaw },
                        )
                        if (showSaw) {
                            Text(
                                lastSaw,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(top = 4.dp),
                            )
                        }
                    }
                }
            } else if (lastTools.isNotEmpty()) {
                // What it actually did. A long pause with no explanation is
                // indistinguishable from a hang; "searched your shelf" is not.
                item {
                    Text(
                        "used " + lastTools.joinToString(", "),
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(horizontal = 8.dp),
                    )
                }
            }
        }

        error?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }

        if (pending != null) {
            Text(
                "Photo attached. Type what you want to know about it, then Send.",
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box {
                OutlinedButton(
                    onClick = { photoMenu = true },
                    enabled = !busy,
                ) { Text(if (pending == null) "Photo" else "Change") }

                DropdownMenu(
                    expanded = photoMenu,
                    onDismissRequest = { photoMenu = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("Take a photo") },
                        onClick = {
                            photoMenu = false
                            val target = newPhotoTarget(context)
                            photoTarget = target
                            cameraLauncher.launch(target)
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("Choose from gallery") },
                        onClick = {
                            photoMenu = false
                            galleryLauncher.launch(
                                PickVisualMediaRequest(
                                    ActivityResultContracts.PickVisualMedia.ImageOnly
                                )
                            )
                        },
                    )
                    if (pending != null) {
                        DropdownMenuItem(
                            text = { Text("Remove photo") },
                            onClick = {
                                photoMenu = false
                                pending = null
                            },
                        )
                    }
                }
            }
            Spacer(Modifier.padding(2.dp))
            // Was only reachable through Tools, where nobody found it.
            OutlinedButton(
                onClick = { pickDoc.launch("*/*") },
                enabled = !busy && myShelf != null,
            ) { Text("Doc") }
            Spacer(Modifier.padding(3.dp))
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Ask something") },
                enabled = !busy,
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            )
            Spacer(Modifier.padding(4.dp))
            Button(onClick = { send() }, enabled = !busy && draft.isNotBlank()) {
                Text("Send")
            }
        }
    }
}

/**
 * One waiting question, with a Yes and a No.
 *
 * BOTH STRINGS ARE SHOWN. Approving only the tidy version would be
 * approving a translation nobody was shown -- the whole value of this queue
 * is that a person sees what a tag said and what the catalogue would make
 * of it, side by side, before either becomes a record.
 *
 * NO DEFAULT AND NO EMPHASIS. Neither button is styled as the expected
 * answer: "no" here is a perfectly good outcome, and a filled Yes next to a
 * plain No is a thumb on the scale.
 */
@Composable
private fun ApprovalCard(
    approval: ChatClient.Approval,
    busy: Boolean,
    onDecide: (Boolean) -> Unit,
) {
    val colours = MaterialTheme.colorScheme
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(colours.surfaceVariant, RoundedCornerShape(16.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            approval.field.replace('_', ' '),
            style = MaterialTheme.typography.labelMedium,
            color = colours.primary,
        )
        Text(
            "The source said \u201C${approval.raw}\u201D.",
            style = MaterialTheme.typography.bodyMedium,
            color = colours.onSurfaceVariant,
        )
        Text(
            "Record it as \u201C${approval.suggested}\u201D?",
            style = MaterialTheme.typography.bodyLarge,
            color = colours.onSurface,
        )
        if (approval.confidence > 0) {
            Text(
                "${(approval.confidence * 100).toInt()}% sure" +
                    if (approval.source.isNotBlank())
                        " \u00B7 " + approval.source.substringBefore(',')
                            .removePrefix("https://").removePrefix("www.")
                            .substringBefore('/')
                    else "",
                style = MaterialTheme.typography.bodySmall,
                color = colours.onSurfaceVariant,
            )
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.padding(top = 4.dp),
        ) {
            OutlinedButton(onClick = { onDecide(true) }, enabled = !busy) {
                Text(if (busy) "Saving\u2026" else "Yes")
            }
            OutlinedButton(onClick = { onDecide(false) }, enabled = !busy) {
                Text("No")
            }
        }
    }
}

@Composable
private fun Bubble(text: String, mine: Boolean) {
    val colours = MaterialTheme.colorScheme
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = if (mine) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        if (mine) {
            // What the person typed, shown as they typed it. Running their own
            // words through a Markdown pass would silently rewrite them --
            // asterisks in a question would vanish, which is confusing when
            // the question was ABOUT asterisks.
            Text(
                text = text,
                modifier = Modifier
                    .widthIn(max = 300.dp)
                    .background(colours.primaryContainer, RoundedCornerShape(18.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = colours.onPrimaryContainer,
            )
        } else {
            // Wider than the person's own bubble: an answer is usually a
            // procedure, and a numbered list re-wrapping every few words is
            // the thing that made these hard to follow.
            MarkdownText(
                text = text,
                color = colours.onSurfaceVariant,
                modifier = Modifier
                    .widthIn(max = 340.dp)
                    .background(colours.surfaceVariant, RoundedCornerShape(18.dp))
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            )
        }
    }
}

/** A tap target without the ripple, for text that acts as a link. */
private fun Modifier.clickableNoRipple(onClick: () -> Unit): Modifier =
    this.clickable(
        interactionSource = MutableInteractionSource(),
        indication = null,
        onClick = onClick,
    )

/** The household's own change-password page. One place, both apps. */
private const val CHANGE_PASSWORD_URL =
    "https://example.org/auth/change"
