package nl.heim.mimir.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import nl.heim.mimir.model.HealthState

@Composable
fun HealthBadge(
    state: HealthState,
    modifier: Modifier = Modifier,
) {
    val color = when (state) {
        HealthState.Ok -> Color(0xFF4A7C59)
        HealthState.Degraded -> Color(0xFFD4A017)
        HealthState.Offline -> Color(0xFFB54A4A)
    }
    Box(
        modifier = modifier
            .size(10.dp)
            .background(color, CircleShape),
    )
}
