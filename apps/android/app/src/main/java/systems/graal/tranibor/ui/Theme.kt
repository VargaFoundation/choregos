package systems.graal.tranibor.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// The console's Radix scales (ADR 0043, 0048): iris for the accent, slate for everything else.
private val light = lightColorScheme(
    primary = Color(0xFF5B5BD6),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFF0F1FE),
    onPrimaryContainer = Color(0xFF5753C6),
    secondary = Color(0xFF60646C),
    background = Color(0xFFFCFCFD),
    onBackground = Color(0xFF1C2024),
    surface = Color(0xFFFCFCFD),
    onSurface = Color(0xFF1C2024),
    surfaceVariant = Color(0xFFF0F0F3),
    onSurfaceVariant = Color(0xFF60646C),
    outline = Color(0xFF8B8D98),
    outlineVariant = Color(0xFFD9D9E0),
    error = Color(0xFFD13438),
)

private val dark = darkColorScheme(
    primary = Color(0xFF6E6ADE),
    onPrimary = Color.White,
    primaryContainer = Color(0xFF202248),
    onPrimaryContainer = Color(0xFFB1A9FF),
    secondary = Color(0xFFB0B4BA),
    background = Color(0xFF111113),
    onBackground = Color(0xFFEDEEF0),
    surface = Color(0xFF111113),
    onSurface = Color(0xFFEDEEF0),
    surfaceVariant = Color(0xFF212225),
    onSurfaceVariant = Color(0xFFB0B4BA),
    outline = Color(0xFF696E77),
    outlineVariant = Color(0xFF363A3F),
    error = Color(0xFFFF6369),
)

@Composable
fun TraniborTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (darkTheme) dark else light, content = content)
}
