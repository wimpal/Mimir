package nl.heim.mimir.ui.chat

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import nl.heim.mimir.model.VoicePhase

@Composable
fun PttMicButton(
    voicePhase: VoicePhase,
    enabled: Boolean,
    onPress: () -> Unit,
    onRelease: () -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val recording = voicePhase == VoicePhase.Recording
    val active = enabled || recording
    val tint = when {
        recording -> MaterialTheme.colorScheme.error
        !active -> MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
        else -> MaterialTheme.colorScheme.primary
    }
    // IconButton steals pointer events — use Box + pointerInput only for hold-to-talk.
    Box(
        modifier = modifier
            .size(48.dp)
            .semantics { contentDescription = "Hold to talk" }
            .pointerInput(active) {
                if (!active) return@pointerInput
                detectTapGestures(
                    onPress = {
                        onPress()
                        val released = tryAwaitRelease()
                        if (released) {
                            onRelease()
                        } else {
                            onCancel()
                        }
                    },
                )
            },
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            Icons.Default.Mic,
            contentDescription = null,
            tint = tint,
        )
    }
}
