package org.example.cutthefluff.ui

import android.net.Uri
import android.widget.VideoView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.delay
import org.example.cutthefluff.R

/**
 * The longest the insignia may hold the app.
 *
 * A BACKSTOP, NOT THE LENGTH OF THE FILM. It runs four seconds and then
 * moves on by itself. This is for the cases where it never does -- a codec
 * the device will not decode, a player reporting neither completion nor
 * error -- because an intro must never become a way to be locked out.
 */
private const val CEILING_MILLIS = 7_000L

/**
 * The mark's own ground, painted whatever theme the phone is in.
 *
 * The animation is drawn on near-black and knows nothing about light mode.
 * Letting the app's ground show through would put a near-white band around
 * a black film in light mode. Its own ground travels with it.
 */
private val MARK_GROUND = Color(0xFF141018)

/**
 * The atom-to-supernova insignia, in front of the app.
 *
 * JASON, 2026-09-26: "can we use the At and Nova explosion that we had for
 * Peewee. I want to have that for the intro for cut the fluff."
 *
 * THE SAME MARK, NOT A COPY OF IT. It is rendered by the same
 * `intro_fx.py` that fronts the Pee-Wee film, from a named palette taken
 * from this app's own dark theme -- which is exactly what that file was
 * built for: "a second project is four values and no edits below". Nothing
 * about the animation was restyled; it was given four colours and a length.
 *
 * SKIPPABLE ANYWHERE, like the garden app's. There is a button, and the
 * whole screen is a target, because the only thing worse than an intro is
 * one you have to aim at to dismiss.
 *
 * ONCE PER LAUNCH. The caller holds the flag, so signing in or coming back
 * from the camera does not replay it; only a cold start does.
 */
@Composable
fun IntroScreen(onDone: () -> Unit) {
    // Guards against onDone arriving twice -- the film ending at the same
    // moment Skip is tapped would otherwise advance two screens.
    var finished by remember { mutableStateOf(false) }
    val done = {
        if (!finished) {
            finished = true
            onDone()
        }
    }

    LaunchedEffect(Unit) {
        delay(CEILING_MILLIS)
        done()
    }

    // No ripple: this is a film being tapped away, and a grey circle
    // spreading across it looks like a fault.
    val noIndication = remember { MutableInteractionSource() }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MARK_GROUND)
            .clickable(
                interactionSource = noIndication,
                indication = null,
                onClick = done,
            ),
        contentAlignment = Alignment.Center,
    ) {
        // The poster sits underneath, always. A VideoView paints nothing
        // until its first frame is decoded, which on a cold start is long
        // enough to read as a flash of flat colour. This is also what is
        // left if the film cannot be played at all.
        Image(
            painter = painterResource(R.drawable.intro_insignia),
            contentDescription = "Cut The Fluff",
            modifier = Modifier.fillMaxSize(),
            // CROP, not fit: the mark is drawn 9:16 for a phone and is
            // centred, so filling the screen is right and nothing of the
            // symbol is lost. Fitting would letterbox it on a taller phone.
            contentScale = ContentScale.Crop,
        )

        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { context ->
                VideoView(context).apply {
                    setVideoURI(
                        Uri.parse(
                            "android.resource://${context.packageName}/"
                                + R.raw.intro_insignia
                        )
                    )
                    setOnPreparedListener { player ->
                        // Silent, and deliberately so at both ends: the
                        // mark is rendered without audio because a sting
                        // fights whatever it sits in front of, and an app
                        // that makes noise on launch in a quiet room is a
                        // reason to uninstall it.
                        player.setVolume(0f, 0f)
                        start()
                    }
                    setOnCompletionListener { done() }
                    // A device that cannot decode it keeps the poster and
                    // moves on, rather than showing the system's "Can't
                    // play this video" dialog over the top.
                    setOnErrorListener { _, _, _ ->
                        done()
                        true
                    }
                }
            },
            onRelease = { it.stopPlayback() },
        )

        TextButton(
            onClick = done,
            modifier = Modifier
                .align(Alignment.TopEnd)
                // The button, unlike the film, has to clear the clock.
                .windowInsetsPadding(WindowInsets.systemBars)
                .padding(8.dp),
        ) {
            Text(
                "Skip",
                style = MaterialTheme.typography.labelLarge,
                // Fixed to the mark's own palette, not the theme's: this
                // screen is always dark, so a theme-coloured label would be
                // near-black on near-black in light mode.
                color = Color(0xFFC9B8EC),
            )
        }
    }
}
