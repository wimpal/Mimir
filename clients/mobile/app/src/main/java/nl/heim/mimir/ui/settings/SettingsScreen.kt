package nl.heim.mimir.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import nl.heim.mimir.model.HealthState
import nl.heim.mimir.ui.components.HealthBadge

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onConfigured: () -> Unit,
    showContinue: Boolean,
) {
    val state by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Mimir Settings") })
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                "Home URL (LAN)",
                style = MaterialTheme.typography.labelLarge,
            )
            OutlinedTextField(
                value = state.brainLanUrl,
                onValueChange = viewModel::onLanUrlChange,
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("http://192.168.1.157:8000") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )

            Text(
                "Away URL (Tailscale)",
                style = MaterialTheme.typography.labelLarge,
            )
            OutlinedTextField(
                value = state.brainAwayUrl,
                onValueChange = viewModel::onAwayUrlChange,
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("http://100.x.y.z:8000") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )

            Text(
                "Bearer token",
                style = MaterialTheme.typography.labelLarge,
            )
            OutlinedTextField(
                value = state.authToken,
                onValueChange = viewModel::onTokenChange,
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("MIMIR_CLIENT_TOKEN from PC .env") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
            )

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                state.health?.let { health ->
                    HealthBadge(state = health.state)
                    val label = when (health.state) {
                        HealthState.Ok -> "Brain reachable"
                        HealthState.Degraded -> "Degraded (${health.detail.ifEmpty { health.status }})"
                        HealthState.Offline -> "Brain offline"
                    }
                    Text(label, style = MaterialTheme.typography.bodyMedium)
                }
            }

            state.activeEndpointLabel?.let { label ->
                Text(
                    label,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }

            state.errorMessage?.let { msg ->
                Text(msg, color = MaterialTheme.colorScheme.error)
            }
            if (state.saveSuccess) {
                Text("Settings saved.", color = MaterialTheme.colorScheme.primary)
            }

            Button(
                onClick = viewModel::save,
                enabled = !state.isSaving,
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (state.isSaving) {
                    CircularProgressIndicator(modifier = Modifier.height(20.dp))
                } else {
                    Text("Save & test connection")
                }
            }

            if (showContinue && state.health?.state == HealthState.Ok) {
                Button(
                    onClick = onConfigured,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Continue to chat")
                }
            }

            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "Home Wi‑Fi: tries LAN URL first. Away from home: falls back to Tailscale URL. " +
                    "Use the PC's 100.x Tailscale IP for away (MagicDNS may fail in-app). " +
                    "At least one URL required.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
