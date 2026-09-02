package nl.heim.mimir.model

sealed class SseEvent {
    data class Meta(val conversationId: String?) : SseEvent()

    data class Token(val text: String) : SseEvent()

    data class Sentence(val index: Int, val text: String) : SseEvent()

    data class ToolStart(val name: String) : SseEvent()

    data object ToolEnd : SseEvent()

    data class Done(
        val conversationId: String?,
        val toolsUsed: List<String>,
        val stoppedReason: String?,
    ) : SseEvent()

    data class ErrorEvent(
        val message: String,
        val conversationId: String?,
    ) : SseEvent()

    data class Unknown(val type: String) : SseEvent()
}

enum class HealthState {
    Ok,
    Degraded,
    Offline,
}

data class HealthInfo(
    val state: HealthState,
    val status: String,
    val detail: String = "",
)
