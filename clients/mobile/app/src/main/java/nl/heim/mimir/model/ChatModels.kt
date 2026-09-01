package nl.heim.mimir.model

enum class MessageRole {
    User,
    Assistant,
    Error,
    System,
}

data class ChatMessage(
    val id: String,
    val role: MessageRole,
    val content: String,
    val isStreaming: Boolean = false,
    val toolsUsed: List<String> = emptyList(),
    val showWriteConfirm: Boolean = false,
    val retryText: String? = null,
)

data class ConversationSummary(
    val id: String,
    val createdAt: String,
    val updatedAt: String,
    val preview: String,
    val messageCount: Int,
)

data class StoredMessage(
    val role: String,
    val content: String,
    val createdAt: String?,
)
