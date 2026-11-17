package org.example.garden.app

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.example.garden.auth.NotSignedInException

/**
 * Everything that is not chat: adding a document to a shelf, seeing the
 * career options, and sending feedback.
 *
 * ONE SCREEN RATHER THAN THREE, because each of these is a handful of
 * controls and a phone with four tabs for five buttons is worse than a
 * page you scroll.
 */
@Composable
fun ToolsScreen(
    api: AppApi,
    isZee: Boolean,
    onBack: () -> Unit,
    onNeedsSignIn: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var shelves by remember { mutableStateOf<List<AppApi.Shelf>>(emptyList()) }
    var chosen by remember { mutableStateOf<AppApi.Shelf?>(null) }
    var shelfMenu by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }

    var awards by remember { mutableStateOf<List<AppApi.Award>>(emptyList()) }
    var awardsLoaded by remember { mutableStateOf(false) }

    var supportKind by remember { mutableStateOf("feedback") }
    var supportText by remember { mutableStateOf("") }

    fun fail(e: Throwable) {
        if (e is NotSignedInException) onNeedsSignIn()
        else error = e.message?.takeIf { it.isNotBlank() }
            ?: "${e.javaClass.simpleName} (no detail)"
    }

    LaunchedEffect(Unit) {
        runCatching { api.shelves() }
            .onSuccess { shelves = it; chosen = it.firstOrNull() }
            .onFailure { fail(it) }
    }

    // ACTION_GET_CONTENT rather than a media picker: these are documents, and
    // the photo picker cannot see a PDF sitting in Downloads.
    val pickFile = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        val shelf = chosen
        if (uri == null || shelf == null) return@rememberLauncherForActivityResult
        busy = true
        error = ""
        status = "Uploading…"
        scope.launch {
            runCatching { api.upload(context, shelf.path, uri) }
                .onSuccess { r ->
                    status = buildString {
                        if (r.saved.isNotEmpty()) append("Added ${r.saved.joinToString(", ")}. ")
                        if (r.skipped.isNotEmpty()) append("Skipped: ${r.skipped.joinToString("; ")}. ")
                        append(r.note)
                    }
                }
                .onFailure { status = ""; fail(it) }
            busy = false
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .imePadding()
            .navigationBarsPadding()
            .padding(16.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text("Tools", style = MaterialTheme.typography.titleMedium)
            Text(
                "Back to chat",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .padding(4.dp)
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = onBack,
                    ),
            )
        }

        Spacer(Modifier.height(20.dp))

        // ---------------------------------------------------------- upload
        Text("Add a document", style = MaterialTheme.typography.titleSmall)
        Text(
            "PDFs, Word files, epubs. Once indexed you can ask questions about it, "
                + "and answers cite the page.",
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(8.dp))

        OutlinedButton(onClick = { shelfMenu = true }, enabled = !busy && shelves.isNotEmpty()) {
            Text(chosen?.label ?: if (shelves.isEmpty()) "No shelves available" else "Choose a shelf")
        }
        DropdownMenu(expanded = shelfMenu, onDismissRequest = { shelfMenu = false }) {
            shelves.forEach { sh ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(sh.label)
                            // Named so uploading into somebody else's shelf
                            // can never feel like an accident.
                            if (sh.sharedWith.isNotEmpty()) {
                                Text(
                                    "also visible to ${sh.sharedWith.joinToString(", ")}",
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            }
                        }
                    },
                    onClick = { chosen = sh; shelfMenu = false },
                )
            }
        }

        Spacer(Modifier.height(8.dp))
        Button(
            onClick = { pickFile.launch("*/*") },
            enabled = !busy && chosen != null,
        ) { Text("Choose a file") }

        if (busy) {
            Spacer(Modifier.height(10.dp))
            CircularProgressIndicator()
        }
        if (status.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(status, style = MaterialTheme.typography.bodySmall)
        }

        Spacer(Modifier.height(24.dp))
        Divider()
        Spacer(Modifier.height(24.dp))

        // ---------------------------------------------------------- awards
        if (isZee) {
            Text("Career options", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            if (!awardsLoaded) {
                OutlinedButton(onClick = {
                    scope.launch {
                        runCatching { api.awards() }
                            .onSuccess { awards = it; awardsLoaded = true }
                            .onFailure { fail(it) }
                    }
                }) { Text("Show awards and promotions") }
            } else {
                Text(
                    "${awards.size} options",
                    style = MaterialTheme.typography.labelSmall,
                )
                Spacer(Modifier.height(6.dp))
                awards.take(40).forEach { a ->
                    Column(modifier = Modifier.padding(vertical = 5.dp)) {
                        Text(a.name, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            listOfNotNull(
                                a.status.takeIf { it.isNotBlank() },
                                a.tier.takeIf { it.isNotBlank() }?.let { "tier $it" },
                            ).joinToString(" · "),
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
                Text(
                    "Progress is recorded on the portal, not here -- it writes into a "
                        + "promotion record, which is not something to change by accident.",
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            Spacer(Modifier.height(24.dp))
            Divider()
            Spacer(Modifier.height(24.dp))
        }

        // --------------------------------------------------------- support
        Text("Feedback or help", style = MaterialTheme.typography.titleSmall)
        Text(
            "Goes to Jason by email. He can reply to you directly.",
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(8.dp))

        Row {
            listOf("feedback" to "Feedback", "help" to "Help", "bug" to "Bug").forEach { (k, label) ->
                OutlinedButton(
                    onClick = { supportKind = k },
                    enabled = supportKind != k,
                    modifier = Modifier.padding(end = 6.dp),
                ) { Text(label) }
            }
        }

        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = supportText,
            onValueChange = { supportText = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("What would help?") },
            minLines = 3,
            enabled = !busy,
        )
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = {
                busy = true; error = ""; status = ""
                scope.launch {
                    runCatching { api.support(supportKind, supportText.trim()) }
                        .onSuccess { status = it; supportText = "" }
                        .onFailure { fail(it) }
                    busy = false
                }
            },
            enabled = !busy && supportText.isNotBlank(),
        ) { Text("Send") }

        if (error.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }

        Spacer(Modifier.height(32.dp))
    }
}
