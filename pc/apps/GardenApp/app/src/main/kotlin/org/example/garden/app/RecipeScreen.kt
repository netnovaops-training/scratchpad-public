package org.example.garden.app

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import org.example.garden.auth.NotSignedInException

/**
 * Cooking from a recipe, rather than reading one.
 *
 * WHO THIS IS SHAPED FOR. Somebody standing at a counter with their hands
 * full, looking up between actions -- Mom, specifically. That drives every
 * decision here and they are not the decisions a recipe website makes:
 *
 *   ONE STEP FILLS THE SCREEN. A numbered list means finding your place
 *   again every time you look up, and losing it is how a step gets skipped.
 *   One large step and a Next button cannot be lost.
 *
 *   INGREDIENTS ARE A CHECKLIST FIRST. Tapping one strikes it through, so
 *   "have I put the oregano in" has an answer that is not memory.
 *
 *   PROGRESS IS VISIBLE. "Step 3 of 5" and a bar, because a recipe with no
 *   end in sight feels longer than one with four steps left.
 *
 * NO GENERATED PICTURES, and that is a limitation rather than a choice. This
 * household has vision models, which READ images, and nothing that makes
 * them -- so there is no way to illustrate a step that does not already have
 * a photo. Where the recipe carries one it is shown; where it does not,
 * nothing is invented. A wrong picture of a step is worse than none.
 */
@Composable
fun RecipeScreen(
    api: AppApi,
    onBack: () -> Unit,
    onNeedsSignIn: () -> Unit,
) {
    val scope = rememberCoroutineScope()

    var query by remember { mutableStateOf("") }
    var list by remember { mutableStateOf<List<AppApi.RecipeSummary>>(emptyList()) }
    var open by remember { mutableStateOf<AppApi.Recipe?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }

    fun fail(e: Throwable) {
        loading = false
        if (e is NotSignedInException) onNeedsSignIn()
        else error = e.message?.takeIf { it.isNotBlank() } ?: "Something went wrong."
    }

    fun search() {
        loading = true; error = ""
        scope.launch {
            runCatching { api.recipes(query) }
                .onSuccess { list = it; loading = false }
                .onFailure { fail(it) }
        }
    }

    LaunchedEffect(Unit) { search() }

    open?.let { r ->
        RecipeSteps(recipe = r, onClose = { open = null })
        return
    }

    Column(
        modifier = Modifier.fillMaxSize().navigationBarsPadding().padding(16.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Recipes", style = MaterialTheme.typography.titleMedium)
            Text(
                "Back",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(4.dp).clickable(onClick = onBack),
            )
        }

        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Search recipes") },
                singleLine = true,
            )
            Spacer(Modifier.padding(4.dp))
            Button(onClick = { search() }, enabled = !loading) { Text("Find") }
        }

        if (error.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(error, color = MaterialTheme.colorScheme.error,
                 style = MaterialTheme.typography.bodySmall)
        }

        Spacer(Modifier.height(12.dp))
        if (loading && list.isEmpty()) {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        }

        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(list) { r ->
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            MaterialTheme.colorScheme.surfaceVariant,
                            RoundedCornerShape(12.dp),
                        )
                        .clickable {
                            loading = true; error = ""
                            scope.launch {
                                runCatching { api.recipe(r.id) }
                                    .onSuccess { open = it; loading = false }
                                    .onFailure { fail(it) }
                            }
                        }
                        .padding(14.dp),
                ) {
                    Text(r.name, style = MaterialTheme.typography.bodyLarge)
                    if (r.course.isNotBlank()) {
                        Text(r.course, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun RecipeSteps(recipe: AppApi.Recipe, onClose: () -> Unit) {
    // -1 is the ingredient checklist, which comes BEFORE step 1: nobody
    // should discover a missing ingredient at step 4.
    var index by remember(recipe.id) { mutableStateOf(-1) }
    val ticked = remember(recipe.id) { mutableStateListOf<Int>() }

    val total = recipe.steps.size

    Column(
        modifier = Modifier.fillMaxSize().navigationBarsPadding().padding(20.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                recipe.name,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            Text(
                "Close",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 8.dp).clickable(onClick = onClose),
            )
        }

        if (recipe.parsedBy == "fallback") {
            // Said plainly rather than hidden: these steps were split by
            // punctuation because the model could not be reached, so they
            // may be in odd places.
            Text(
                "The steps were split roughly -- the assistant was unreachable.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
            )
        }

        Spacer(Modifier.height(16.dp))

        if (index >= 0 && total > 0) {
            LinearProgressIndicator(
                progress = { (index + 1f) / total },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "Step ${index + 1} of $total",
                style = MaterialTheme.typography.labelMedium,
            )
            Spacer(Modifier.height(20.dp))
        }

        Box(modifier = Modifier.weight(1f)) {
            if (index < 0) {
                Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                    Text("What you need", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(10.dp))
                    recipe.ingredients.forEachIndexed { i, ing ->
                        val done = i in ticked
                        Text(
                            text = ing,
                            // Big enough to read from arm's length on a
                            // counter, which is where this is used.
                            fontSize = 17.sp,
                            lineHeight = 26.sp,
                            textDecoration = if (done) TextDecoration.LineThrough else null,
                            color = if (done) MaterialTheme.colorScheme.outline
                                    else MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { if (done) ticked.remove(i) else ticked.add(i) }
                                .padding(vertical = 9.dp),
                        )
                    }
                    if (recipe.ingredients.isEmpty()) {
                        Text("No ingredients were listed for this one.",
                             style = MaterialTheme.typography.bodyMedium)
                    }
                }
            } else {
                Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                    Text(
                        recipe.steps[index],
                        fontSize = 22.sp,
                        lineHeight = 32.sp,
                    )
                }
            }
        }

        Spacer(Modifier.height(16.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            OutlinedButton(
                onClick = { if (index >= 0) index -= 1 },
                enabled = index >= 0,
            ) { Text("Back") }

            if (index < total - 1) {
                Button(onClick = { index += 1 }) {
                    Text(if (index < 0) "Start cooking" else "Next")
                }
            } else {
                Button(onClick = onClose) { Text("Done") }
            }
        }
    }
}
