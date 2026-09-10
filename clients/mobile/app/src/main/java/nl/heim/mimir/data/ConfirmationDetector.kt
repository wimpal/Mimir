package nl.heim.mimir.data

/**
 * Client-side heuristic for M3 write confirmation prompts.
 * Mirrors intent patterns from brain/mcp/write_guard.py (subset).
 */
object ConfirmationDetector {
    private val confirmQuestionPattern = Regex(
        "(?i)(shall i|should i|do you want|confirm|go ahead|zal ik|wil je dat ik|" +
            "mag ik|bevestig|klopt dat|is dat goed|voeg ik|toevoegen\\?|" +
            "save\\s+(this|the)\\s+recipe|bewaar\\s+(dit|het)\\s+recept)",
    )

    private val recipeSavePatterns = listOf(
        Regex("(?i)\\bsave\\b.*\\brecipe\\b"),
        Regex("(?i)\\brecipe\\b.*\\bsave\\b"),
        Regex("(?i)\\badd\\b.*\\brecipe\\b"),
        Regex("(?i)\\bimport\\b.*\\brecipe\\b"),
        Regex("(?i)\\bbewaar\\b.*\\brecept\\b"),
        Regex("(?i)\\bvoeg\\b.*\\brecept\\b.*\\btoe\\b"),
        Regex("(?i)\\brecept\\s+opslaan\\b"),
        Regex("(?i)\\bsla\\b.*\\brecept\\b.*\\bop\\b"),
        Regex("(?i)\\bimporteer\\b.*\\brecept\\b"),
        Regex("(?i)\\bimport\\b.*\\brecipe\\b"),
    )

    private val mutationPatterns = listOf(
        Regex("(?i)\\badd\\b.*\\b(to\\s+the\\s+)?(shopping\\s+)?list\\b"),
        Regex("(?i)\\bvoeg\\b.*\\b(toe|toe aan)\\b"),
        Regex("(?i)\\bwe\\s+spent\\b"),
        Regex("(?i)\\b€\\s*\\d"),
        Regex("(?i)\\b(betaald|uitgegeven|gekocht)\\b"),
        Regex("(?i)\\brecord\\b.*\\b(expense|transaction|uitgave)\\b"),
    )

    private val readOnlyPatterns = listOf(
        Regex("(?i)\\bwhat('s| is)\\s+(on\\s+the\\s+)?(shopping\\s+)?list\\b"),
        Regex("(?i)\\bwhat('s| is)\\s+low\\b"),
        Regex("(?i)\\bhow\\s+much\\s+(did\\s+we\\s+)?spend"),
        Regex("(?i)\\bwhat\\s+can\\s+(i|we)\\s+cook\\b"),
        Regex("(?i)\\brecept\\s+met\\b"),
        Regex("(?i)\\bwat\\s+kunnen\\s+we\\s+koken\\b"),
    )

    /** Tools that do not mean a write already happened (T-021 URL fetch + stage). */
    private val readOnlyTools = setOf(
        "web.fetch",
        "homebase.recipes.search",
        "homebase.recipes.get",
        "homebase.inventory.list",
        "homebase.shopping_list.list",
        "homebase.tasks.list",
        "homebase.lights.list",
        "get_weather",
        "get_calendar",
        "get_server_time",
        // Staged add returns awaiting_confirmation — Confirm buttons must still show.
        "homebase.recipes.add",
    )

    fun userMessageRequestsWrite(text: String): Boolean {
        val normalized = text.trim()
        if (normalized.isEmpty()) return false
        if (recipeSavePatterns.any { it.containsMatchIn(normalized) }) return true
        if (readOnlyPatterns.any { it.containsMatchIn(normalized) }) return false
        return mutationPatterns.any { it.containsMatchIn(normalized) }
    }

    fun shouldShowWriteConfirm(
        assistantText: String,
        toolsUsed: List<String>,
        priorUserMessage: String?,
    ): Boolean {
        val writeToolsUsed = toolsUsed.filterNot { it in readOnlyTools }
        if (writeToolsUsed.isNotEmpty()) return false
        val text = assistantText.trim()
        if (text.isEmpty()) return false
        if (!confirmQuestionPattern.containsMatchIn(text)) return false
        if (priorUserMessage != null && userMessageRequestsWrite(priorUserMessage)) {
            return true
        }
        // Fallback: question mark + confirm phrase is enough when user asked a mutation.
        return text.contains('?') && confirmQuestionPattern.containsMatchIn(text)
    }

    fun confirmReply(assistantText: String): String {
        return if (looksDutch(assistantText)) "ja" else "yes"
    }

    fun cancelReply(assistantText: String): String {
        return if (looksDutch(assistantText)) "nee" else "no"
    }

    private val spokenConfirmPattern = Regex(
        "(?i)^(ja|yes|yep|yeah|ok|okay|correct|bevestig|klopt)$",
    )

    private val spokenCancelPattern = Regex(
        "(?i)^(nee|no|nope|cancel|annuleer|stop)$",
    )

    fun isSpokenConfirm(text: String): Boolean {
        val normalized = text.trim().trimEnd('.', '!', '?')
        return spokenConfirmPattern.matches(normalized)
    }

    fun isSpokenCancel(text: String): Boolean {
        val normalized = text.trim().trimEnd('.', '!', '?')
        return spokenCancelPattern.matches(normalized)
    }

    fun localeForTts(language: String?): String {
        return if (language?.lowercase()?.startsWith("nl") == true) "nl" else "en"
    }

    private fun looksDutch(text: String): Boolean {
        val lower = text.lowercase()
        val dutchHints = listOf(
            "zal ik", "wil je", "mag ik", "bevestig", "boodschappen",
            "uitgave", "meneer", "recept", "bewaar",
        )
        return dutchHints.any { lower.contains(it) }
    }
}
