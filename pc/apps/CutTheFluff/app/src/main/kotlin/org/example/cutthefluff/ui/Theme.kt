package org.example.cutthefluff.ui

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/**
 * The app's own colours: a soft lavender rather than white.
 *
 * WHY NOT THE DEFAULT. Material 3's baseline light scheme is very nearly
 * white, and a white page on a phone held at arm's length in a kitchen is
 * glare -- the text is fine, the background is what tires the eye. A tinted
 * ground lowers the contrast between screen and room without touching the
 * contrast between text and background, which is the one that matters for
 * legibility.
 *
 * DYNAMIC COLOUR IS DELIBERATELY OFF. Material You would recolour this from
 * the phone's wallpaper, so the app would look different on every handset and
 * could land on a scheme nobody chose -- including a low-contrast one. This
 * app is used by several people on several phones and should look the same on
 * all of them.
 *
 * CONTRAST WAS CHECKED, NOT EYEBALLED. Body text (#1C1725) on the lavender
 * ground (#F4F1FA) is about 14:1, far past the 4.5:1 that WCAG asks for, so
 * the calmer background costs nothing in readability. The muted purple
 * primary (#5B4B8A) on that same ground is about 7:1, which keeps buttons and
 * links legible rather than merely tasteful.
 */

private val LightColours = lightColorScheme(
    // The ground. Light enough to read black text against, tinted enough that
    // it is not a lightbulb.
    background = Color(0xFFF4F1FA),
    onBackground = Color(0xFF1C1725),

    // Cards and the assistant's bubbles sit just above the ground.
    surface = Color(0xFFFAF8FE),
    onSurface = Color(0xFF1C1725),
    surfaceVariant = Color(0xFFE8E2F4),
    onSurfaceVariant = Color(0xFF241E30),

    primary = Color(0xFF5B4B8A),
    onPrimary = Color(0xFFFFFFFF),
    // The person's own chat bubbles, and the update banner.
    primaryContainer = Color(0xFFE2D9F6),
    onPrimaryContainer = Color(0xFF1E1633),

    secondary = Color(0xFF6E6484),
    onSecondary = Color(0xFFFFFFFF),
    outline = Color(0xFF7C7391),

    error = Color(0xFF8E2C3B),
    onError = Color(0xFFFFFFFF),
)

private val DarkColours = darkColorScheme(
    // Not black: a very dark violet, so the two themes feel like the same app
    // rather than two apps.
    background = Color(0xFF141018),
    onBackground = Color(0xFFE9E4F2),

    surface = Color(0xFF1B1622),
    onSurface = Color(0xFFE9E4F2),
    surfaceVariant = Color(0xFF2B2437),
    onSurfaceVariant = Color(0xFFDDD6EC),

    primary = Color(0xFFC9B8EC),
    onPrimary = Color(0xFF2A1F45),
    primaryContainer = Color(0xFF3E3260),
    onPrimaryContainer = Color(0xFFEBE2FF),

    secondary = Color(0xFFBFB6D4),
    onSecondary = Color(0xFF2C2640),
    outline = Color(0xFF938BA8),

    error = Color(0xFFFFB3B8),
    onError = Color(0xFF5A1420),
)

@Composable
fun CutTheFluffTheme(
    dark: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colours = if (dark) DarkColours else LightColours
    val view = LocalView.current

    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            // The status bar takes the app's ground colour rather than staying
            // white above a lavender screen, which otherwise reads as a strip
            // of glare the app forgot about.
            window.statusBarColor = colours.background.toArgb()
            // Icon colour derived from the background's luminance rather than
            // from `dark`: a theme edit that lightens the dark ground would
            // otherwise leave white icons on a pale bar.
            WindowCompat.getInsetsController(window, view)
                .isAppearanceLightStatusBars = colours.background.luminance() > 0.5f
        }
    }

    MaterialTheme(colorScheme = colours, content = content)
}
