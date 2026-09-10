package nl.heim.mimir.data

/**
 * Maps brain SSE `meta.locale` (T-051) to a `/v1/tts` locale.
 * Prefer brain-resolved locale over Whisper STT language.
 */
object TurnLocale {
    fun forTts(metaLocale: String?, fallback: String = "nl"): String {
        val loc = metaLocale?.trim()?.lowercase().orEmpty()
        return when {
            loc == "nl" || loc.startsWith("nl") -> "nl"
            loc == "en" || loc.startsWith("en") -> "en"
            fallback == "en" -> "en"
            else -> "nl"
        }
    }
}
