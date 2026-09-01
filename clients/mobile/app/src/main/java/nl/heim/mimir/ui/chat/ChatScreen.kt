package nl.heim.mimir.ui.chat

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import nl.heim.mimir.model.HealthState
import nl.heim.mimir.model.VoicePhase
import nl.heim.mimir.ui.components.HealthBadge

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    onOpenSettings: () -> Unit,
) {
    val state by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()
    val context = LocalContext.current
    var micGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    var showMicDenied by remember { mutableStateOf(false) }
    val micPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        micGranted = granted
        if (!granted) showMicDenied = true
    }

    LaunchedEffect(state.messages.size, state.messages.lastOrNull()?.content) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.lastIndex)
        }
    }

    if (showMicDenied) {
        AlertDialog(
            onDismissRequest = { showMicDenied = false },
            title = { Text("Microphone permission") },
            text = {
                Text("Mimir needs the microphone for push-to-talk. Grant permission in system settings.")
            },
            confirmButton = {
                TextButton(onClick = { showMicDenied = false }) { Text("OK") }
            },
        )
    }

    if (state.showConversationPicker) {
        AlertDialog(
            onDismissRequest = viewModel::dismissConversationPicker,
            title = { Text("Switch conversation") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (state.conversations.isEmpty()) {
                        Text("No past conversations.")
                    } else {
                        state.conversations.forEach { row ->
                            Text(
                                text = formatConversationLabel(row),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { viewModel.selectConversation(row.id) }
                                    .padding(vertical = 8.dp),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = viewModel::startNewConversation) {
                    Text("New conversation")
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissConversationPicker) {
                    Text("Close")
                }
            },
        )
    }

    val inputBlocked = viewModel.isInputBlocked ||
        state.voicePhase == VoicePhase.Recording

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        HealthBadge(state = state.health?.state ?: HealthState.Offline)
                        Text(
                            text = state.brainUrl,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::startNewConversation) {
                        Icon(Icons.Default.Add, contentDescription = "New chat")
                    }
                    IconButton(onClick = viewModel::refreshHealth) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh health")
                    }
                    IconButton(onClick = viewModel::openConversationPicker) {
                        Icon(Icons.Default.History, contentDescription = "Conversations")
                    }
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            state.workStatus?.let { status ->
                Text(
                    text = if (state.voicePhase == VoicePhase.Speaking) {
                        "$status (tap mic to interrupt)"
                    } else {
                        status
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (state.isLoadingHistory) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator()
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(state.messages, key = { it.id }) { message ->
                        MessageBubble(
                            message = message,
                            onConfirm = { viewModel.confirmWrite(message.id) },
                            onCancel = { viewModel.cancelWrite(message.id) },
                            modifier = Modifier.padding(vertical = 4.dp),
                        )
                    }
                }
            }

            state.pendingRetryText?.let {
                Text(
                    text = "Connection lost — tap to retry",
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { viewModel.retryLastMessage() }
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    color = MaterialTheme.colorScheme.error,
                )
            }

            state.historyBanner?.let { banner ->
                Text(
                    text = banner,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { viewModel.retryHistoryLoad() }
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            OutlinedTextField(
                value = state.inputText,
                onValueChange = viewModel::onInputChange,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                placeholder = { Text("Message Mimir…") },
                enabled = !inputBlocked,
                maxLines = 4,
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PttMicButton(
                    voicePhase = state.voicePhase,
                    enabled = !viewModel.isInputBlocked ||
                        state.voicePhase == VoicePhase.Speaking,
                    onPress = {
                        val granted = ContextCompat.checkSelfPermission(
                            context,
                            Manifest.permission.RECORD_AUDIO,
                        ) == PackageManager.PERMISSION_GRANTED
                        micGranted = granted
                        if (granted) {
                            viewModel.onPttPress()
                        } else {
                            micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    },
                    onRelease = viewModel::onPttRelease,
                    onCancel = viewModel::onPttCancel,
                )
                FloatingActionButton(
                    onClick = {
                        if (!inputBlocked && state.inputText.isNotBlank()) {
                            viewModel.sendMessage()
                        }
                    },
                    modifier = Modifier.padding(start = 8.dp),
                ) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
                }
            }
        }
    }
}
