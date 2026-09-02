package nl.heim.mimir.ui.chat

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import nl.heim.mimir.data.AudioPlayer
import nl.heim.mimir.data.AudioQueue
import nl.heim.mimir.data.AudioRecorder
import nl.heim.mimir.data.BrainApi
import nl.heim.mimir.data.BrainClientError
import nl.heim.mimir.data.ConfirmationDetector
import nl.heim.mimir.data.SettingsRepository
import nl.heim.mimir.model.ChatMessage
import nl.heim.mimir.model.ChatTurnResult
import nl.heim.mimir.model.ConversationSummary
import nl.heim.mimir.model.HealthInfo
import nl.heim.mimir.model.HealthState
import nl.heim.mimir.model.MessageRole
import nl.heim.mimir.model.SseEvent
import nl.heim.mimir.model.VoicePhase
import java.util.UUID
import kotlin.coroutines.resume

data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val inputText: String = "",
    val isStreaming: Boolean = false,
    val workStatus: String? = null,
    val conversationId: String? = null,
    val brainUrl: String = "",
    val health: HealthInfo? = null,
    val showConversationPicker: Boolean = false,
    val conversations: List<ConversationSummary> = emptyList(),
    val isLoadingHistory: Boolean = false,
    val pendingRetryText: String? = null,
    val historyBanner: String? = null,
    val voicePhase: VoicePhase = VoicePhase.Idle,
)

class ChatViewModel(
    private val repository: SettingsRepository,
    appContext: Context,
) : ViewModel() {
    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val audioRecorder = AudioRecorder()
    private val audioPlayer = AudioPlayer(appContext.applicationContext)
    private var streamJob: Job? = null
    private var recordJob: Job? = null
    private var voiceJob: Job? = null
    private var audioQueue: AudioQueue? = null
    private var lastUserMessage: String? = null

    val isInputBlocked: Boolean
        get() {
            val s = _uiState.value
            return s.isStreaming ||
                s.voicePhase == VoicePhase.Transcribing ||
                s.voicePhase == VoicePhase.Chatting ||
                s.voicePhase == VoicePhase.Speaking
        }

    init {
        viewModelScope.launch {
            val settings = repository.settingsFlow.first()
            _uiState.update {
                it.copy(
                    conversationId = settings.conversationId,
                )
            }
            if (settings.isConfigured) {
                refreshHealth()
                settings.conversationId?.let { loadHistory(it) }
            }
        }
    }

    override fun onCleared() {
        audioRecorder.cancel()
        audioPlayer.stop()
        super.onCleared()
    }

    fun onInputChange(value: String) {
        _uiState.update { it.copy(inputText = value) }
    }

    fun sendMessage() {
        val text = _uiState.value.inputText.trim()
        if (text.isEmpty() || isInputBlocked) return
        _uiState.update { it.copy(inputText = "", pendingRetryText = null) }
        sendInternal(text)
    }

    fun confirmWrite(messageId: String) {
        val msg = _uiState.value.messages.find { it.id == messageId } ?: return
        if (isInputBlocked) return
        dismissConfirmOnMessage(messageId)
        sendInternal(ConfirmationDetector.confirmReply(msg.content))
    }

    fun cancelWrite(messageId: String) {
        val msg = _uiState.value.messages.find { it.id == messageId } ?: return
        if (isInputBlocked) return
        dismissConfirmOnMessage(messageId)
        sendInternal(ConfirmationDetector.cancelReply(msg.content))
    }

    fun retryLastMessage() {
        val retry = _uiState.value.pendingRetryText ?: return
        if (isInputBlocked) return
        _uiState.update { it.copy(pendingRetryText = null) }
        sendInternal(retry)
    }

    fun onPttPress() {
        audioQueue?.cancel()
        audioQueue = null
        audioPlayer.stop()
        if (_uiState.value.isStreaming) return
        if (_uiState.value.voicePhase == VoicePhase.Transcribing ||
            _uiState.value.voicePhase == VoicePhase.Chatting
        ) {
            return
        }
        if (_uiState.value.voicePhase == VoicePhase.Speaking) {
            stopSpeaking()
        }
        voiceJob?.cancel()
        recordJob?.cancel()
        audioRecorder.cancel()
        _uiState.update {
            it.copy(voicePhase = VoicePhase.Recording, workStatus = "Recording…")
        }
        recordJob = viewModelScope.launch(Dispatchers.IO) {
            if (!audioRecorder.begin()) {
                withContext(Dispatchers.Main) {
                    _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                    appendError("Microphone unavailable.")
                }
                return@launch
            }
            audioRecorder.captureLoop()
        }
    }

    fun onPttRelease() {
        if (_uiState.value.voicePhase != VoicePhase.Recording) return
        voiceJob = viewModelScope.launch {
            val wav = withContext(Dispatchers.IO) { audioRecorder.finish() }
            if (wav == null) {
                _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                appendError("No speech detected.")
                return@launch
            }
            runVoicePipeline(wav)
        }
    }

    fun onPttCancel() {
        recordJob?.cancel()
        withContextSafeCancelRecording()
    }

    fun stopSpeaking() {
        audioQueue?.cancel()
        audioQueue = null
        audioPlayer.stop()
        _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
    }

    private fun withContextSafeCancelRecording() {
        viewModelScope.launch(Dispatchers.IO) {
            audioRecorder.cancel()
            withContext(Dispatchers.Main) {
                _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
            }
        }
    }

    fun refreshHealth() {
        viewModelScope.launch {
            val settings = repository.settingsFlow.first()
            if (!settings.isConfigured) return@launch
            try {
                val endpoint = repository.resolveBrainEndpoint(settings, forceRefresh = true)
                val health = BrainApi(endpoint.url, settings.authToken).health()
                _uiState.update {
                    it.copy(
                        health = health,
                        brainUrl = endpoint.url,
                    )
                }
                if (health.state == HealthState.Ok) {
                    retryHistoryLoadIfNeeded()
                }
            } catch (_: BrainClientError) {
                _uiState.update {
                    it.copy(
                        health = HealthInfo(
                            state = HealthState.Offline,
                            status = "offline",
                            detail = "unreachable",
                        ),
                    )
                }
            }
        }
    }

    fun retryHistoryLoad() {
        val cid = _uiState.value.conversationId ?: return
        loadHistory(cid)
    }

    private fun retryHistoryLoadIfNeeded() {
        val state = _uiState.value
        val cid = state.conversationId ?: return
        if (state.historyBanner != null || (state.messages.isEmpty() && !state.isLoadingHistory)) {
            loadHistory(cid)
        }
    }

    fun openConversationPicker() {
        viewModelScope.launch {
            val settings = repository.settingsFlow.first()
            if (!settings.isConfigured) return@launch
            try {
                val api = repository.createBrainApi(settings)
                val rows = api.listConversations()
                _uiState.update {
                    it.copy(showConversationPicker = true, conversations = rows)
                }
            } catch (e: BrainClientError) {
                appendError(e.message ?: "Could not load conversations.")
            }
        }
    }

    fun dismissConversationPicker() {
        _uiState.update { it.copy(showConversationPicker = false) }
    }

    fun selectConversation(id: String) {
        viewModelScope.launch {
            repository.saveConversationId(id)
            _uiState.update {
                it.copy(
                    conversationId = id,
                    showConversationPicker = false,
                    messages = emptyList(),
                )
            }
            loadHistory(id)
        }
    }

    fun startNewConversation() {
        streamJob?.cancel()
        voiceJob?.cancel()
        recordJob?.cancel()
        audioQueue?.cancel()
        audioQueue = null
        audioPlayer.stop()
        audioRecorder.cancel()
        viewModelScope.launch {
            repository.saveConversationId(null)
            _uiState.update {
                it.copy(
                    conversationId = null,
                    messages = emptyList(),
                    inputText = "",
                    isStreaming = false,
                    workStatus = null,
                    voicePhase = VoicePhase.Idle,
                    pendingRetryText = null,
                    historyBanner = null,
                    showConversationPicker = false,
                )
            }
        }
    }

    private fun loadHistory(conversationId: String) {
        viewModelScope.launch {
            val settings = repository.settingsFlow.first()
            if (!settings.isConfigured) return@launch
            _uiState.update { it.copy(isLoadingHistory = true, historyBanner = null) }
            try {
                val api = repository.createBrainApi(settings)
                val stored = api.listMessages(conversationId)
                val messages = stored.mapNotNull { row ->
                    val role = when (row.role.lowercase()) {
                        "user" -> MessageRole.User
                        "assistant" -> MessageRole.Assistant
                        else -> return@mapNotNull null
                    }
                    ChatMessage(
                        id = UUID.randomUUID().toString(),
                        role = role,
                        content = row.content,
                    )
                }
                _uiState.update {
                    it.copy(messages = messages, isLoadingHistory = false, historyBanner = null)
                }
            } catch (_: BrainClientError) {
                _uiState.update {
                    it.copy(
                        isLoadingHistory = false,
                        historyBanner = "Brain offline — tap to reload history",
                    )
                }
            }
        }
    }

    private suspend fun runVoicePipeline(wav: ByteArray) {
        _uiState.update {
            it.copy(voicePhase = VoicePhase.Transcribing, workStatus = "Transcribing…")
        }
        val settings = repository.settingsFlow.first()
        val api = repository.createBrainApi(settings)
        val stt = try {
            api.stt(wav)
        } catch (e: BrainClientError) {
            appendError(e.message ?: "Speech recognition failed.")
            _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
            return
        }

        val pending = _uiState.value.messages.lastOrNull { it.showWriteConfirm }
        if (pending != null) {
            when {
                ConfirmationDetector.isSpokenConfirm(stt.text) -> {
                    dismissConfirmOnMessage(pending.id)
                    val result = completeChatTurn(
                        ConfirmationDetector.confirmReply(pending.content),
                        voiceMode = true,
                        ttsLocale = ConfirmationDetector.localeForTts(stt.language),
                    )
                    if (!result.success) {
                        _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                    }
                    return
                }
                ConfirmationDetector.isSpokenCancel(stt.text) -> {
                    dismissConfirmOnMessage(pending.id)
                    val result = completeChatTurn(
                        ConfirmationDetector.cancelReply(pending.content),
                        voiceMode = true,
                        ttsLocale = ConfirmationDetector.localeForTts(stt.language),
                    )
                    if (!result.success) {
                        _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                    }
                    return
                }
            }
        }

        _uiState.update { it.copy(voicePhase = VoicePhase.Chatting) }
        val result = completeChatTurn(
            stt.text,
            voiceMode = true,
            ttsLocale = ConfirmationDetector.localeForTts(stt.language),
        )
        if (!result.success) {
            result.errorMessage?.let { appendError(it) }
            _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
        }
    }

    private suspend fun speakReply(text: String, languageHint: String?) {
        val settings = repository.settingsFlow.first()
        val api = repository.createBrainApi(settings)
        val locale = ConfirmationDetector.localeForTts(languageHint)
        _uiState.update { it.copy(voicePhase = VoicePhase.Speaking, workStatus = "Speaking…") }
        try {
            val wav = api.tts(text, locale)
            suspendCancellableCoroutine { cont ->
                cont.invokeOnCancellation { audioPlayer.stop() }
                audioPlayer.play(wav) {
                    _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                    if (cont.isActive) cont.resume(Unit)
                }
            }
        } catch (e: BrainClientError) {
            appendError(e.message ?: "Speech synthesis failed.")
            _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
        }
    }

    private fun sendInternal(text: String) {
        streamJob?.cancel()
        voiceJob?.cancel()
        streamJob = viewModelScope.launch {
            completeChatTurn(text)
        }
    }

    private suspend fun completeChatTurn(
        text: String,
        voiceMode: Boolean = false,
        ttsLocale: String = "nl",
    ): ChatTurnResult {
        lastUserMessage = text
        val userId = UUID.randomUUID().toString()
        val assistantId = UUID.randomUUID().toString()
        _uiState.update { state ->
            state.copy(
                isStreaming = true,
                workStatus = "Working…",
                pendingRetryText = null,
                messages = state.messages + listOf(
                    ChatMessage(id = userId, role = MessageRole.User, content = text),
                    ChatMessage(
                        id = assistantId,
                        role = MessageRole.Assistant,
                        content = "",
                        isStreaming = true,
                    ),
                ),
            )
        }

        val settings = repository.settingsFlow.first()
        val endpoint = repository.resolveBrainEndpoint(settings)
        val api = BrainApi(endpoint.url, settings.authToken)
        _uiState.update { it.copy(brainUrl = endpoint.url) }
        var assistantText = ""
        var toolsUsed = emptyList<String>()
        var conversationId = _uiState.value.conversationId
        var errorMessage: String? = null
        var sentencesPlayed = false
        val ttsJobs = mutableListOf<Job>()
        val queue = if (voiceMode) {
            AudioQueue(audioPlayer).also {
                audioQueue = it
                it.setOnFirstPlayback {
                    _uiState.update {
                        it.copy(voicePhase = VoicePhase.Speaking, workStatus = "Speaking…")
                    }
                }
            }
        } else {
            null
        }

        try {
            coroutineScope {
                api.streamChat(text, conversationId).collect { event ->
                    when (event) {
                        is SseEvent.Meta -> {
                            event.conversationId?.let { cid ->
                                conversationId = cid
                                repository.saveConversationId(cid)
                                _uiState.update { it.copy(conversationId = cid) }
                            }
                        }
                        is SseEvent.ToolStart -> {
                            _uiState.update { it.copy(workStatus = "${event.name}…") }
                        }
                        is SseEvent.ToolEnd -> {
                            _uiState.update { it.copy(workStatus = "Working…") }
                        }
                        is SseEvent.Token -> {
                            assistantText += event.text
                            updateAssistant(assistantId, assistantText, isStreaming = true)
                        }
                        is SseEvent.Sentence -> {
                            if (voiceMode && queue != null) {
                                sentencesPlayed = true
                                val job = launch(Dispatchers.IO) {
                                    try {
                                        val wav = api.tts(event.text, ttsLocale)
                                        queue.enqueue(event.index, wav)
                                    } catch (e: BrainClientError) {
                                        appendError(e.message ?: "Speech synthesis failed.")
                                        queue.cancel()
                                        audioQueue = null
                                    }
                                }
                                ttsJobs.add(job)
                            }
                        }
                        is SseEvent.ErrorEvent -> {
                            queue?.cancel()
                            audioQueue = null
                            event.conversationId?.let { cid ->
                                conversationId = cid
                                repository.saveConversationId(cid)
                                _uiState.update { it.copy(conversationId = cid) }
                            }
                            errorMessage = event.message
                            appendError(event.message)
                            removeStreamingAssistant(assistantId)
                        }
                        is SseEvent.Done -> {
                            event.conversationId?.let { cid ->
                                conversationId = cid
                                repository.saveConversationId(cid)
                                _uiState.update { it.copy(conversationId = cid) }
                            }
                            toolsUsed = event.toolsUsed
                            val showConfirm = ConfirmationDetector.shouldShowWriteConfirm(
                                assistantText = assistantText,
                                toolsUsed = toolsUsed,
                                priorUserMessage = lastUserMessage,
                            )
                            updateAssistant(
                                id = assistantId,
                                content = assistantText,
                                isStreaming = false,
                                toolsUsed = toolsUsed,
                                showWriteConfirm = showConfirm,
                            )
                        }
                        is SseEvent.Unknown -> Unit
                    }
                }
            }
            ttsJobs.joinAll()
            if (voiceMode && errorMessage == null) {
                if (sentencesPlayed && queue != null) {
                    suspendCancellableCoroutine { cont ->
                        queue.whenIdle {
                            audioQueue = null
                            _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                            if (cont.isActive) cont.resume(Unit)
                        }
                    }
                } else if (assistantText.isNotBlank()) {
                    speakReply(assistantText, ttsLocale)
                } else {
                    _uiState.update { it.copy(voicePhase = VoicePhase.Idle, workStatus = null) }
                }
            }
        } catch (e: BrainClientError) {
            queue?.cancel()
            audioQueue = null
            errorMessage = e.message ?: "Something went wrong."
            if (assistantText.isNotBlank()) {
                updateAssistant(assistantId, assistantText, isStreaming = false)
            } else {
                removeStreamingAssistant(assistantId)
            }
            appendError(errorMessage!!)
            _uiState.update { it.copy(pendingRetryText = text) }
        } finally {
            _uiState.update { it.copy(isStreaming = false, workStatus = null) }
            refreshHealth()
        }

        return ChatTurnResult(
            assistantText = assistantText,
            success = errorMessage == null && assistantText.isNotBlank(),
            errorMessage = errorMessage,
            toolsUsed = toolsUsed,
        )
    }

    private fun dismissConfirmOnMessage(messageId: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map {
                    if (it.id == messageId) it.copy(showWriteConfirm = false) else it
                },
            )
        }
    }

    private fun updateAssistant(
        id: String,
        content: String,
        isStreaming: Boolean,
        toolsUsed: List<String> = emptyList(),
        showWriteConfirm: Boolean = false,
    ) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages.map { msg ->
                    if (msg.id == id) {
                        msg.copy(
                            content = content,
                            isStreaming = isStreaming,
                            toolsUsed = toolsUsed,
                            showWriteConfirm = showWriteConfirm,
                        )
                    } else {
                        msg
                    }
                },
            )
        }
    }

    private fun removeStreamingAssistant(id: String) {
        _uiState.update { state ->
            state.copy(messages = state.messages.filterNot { it.id == id && it.isStreaming })
        }
    }

    private fun appendError(message: String) {
        _uiState.update { state ->
            state.copy(
                messages = state.messages + ChatMessage(
                    id = UUID.randomUUID().toString(),
                    role = MessageRole.Error,
                    content = message,
                ),
            )
        }
    }

    class Factory(
        private val repository: SettingsRepository,
        private val appContext: Context,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return ChatViewModel(repository, appContext) as T
        }
    }
}

fun formatConversationLabel(row: ConversationSummary): String {
    val preview = row.preview.ifBlank { "(no preview)" }
    val updated = row.updatedAt.ifBlank { "?" }
    val shortId = row.id.take(8).ifBlank { "?" }
    return "$preview  ·  $updated  ·  $shortId"
}
