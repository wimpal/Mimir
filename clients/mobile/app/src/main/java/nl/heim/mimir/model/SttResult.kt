package nl.heim.mimir.model

data class SttResult(
    val text: String,
    val language: String?,
)

data class ChatTurnResult(
    val assistantText: String,
    val success: Boolean,
    val errorMessage: String? = null,
    val toolsUsed: List<String> = emptyList(),
)

enum class VoicePhase {
    Idle,
    Recording,
    Transcribing,
    Chatting,
    Speaking,
}
