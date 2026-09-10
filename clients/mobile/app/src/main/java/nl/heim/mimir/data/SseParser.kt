package nl.heim.mimir.data

import org.json.JSONObject

/**
 * Port of [clients.tui.brain_client.parse_sse_chunk].
 */
object SseParser {
    fun parseChunk(buffer: String): Pair<List<JSONObject>, String> {
        val events = mutableListOf<JSONObject>()
        var rest = buffer
        while (true) {
            var sep = -1
            var sepLen = 0
            for (candidate in listOf("\r\n\r\n", "\n\n")) {
                val idx = rest.indexOf(candidate)
                if (idx >= 0 && (sep < 0 || idx < sep)) {
                    sep = idx
                    sepLen = candidate.length
                }
            }
            if (sep < 0) break

            val raw = rest.substring(0, sep)
            rest = rest.substring(sep + sepLen)

            val dataLines = raw.lineSequence()
                .filter { it.startsWith("data:") }
                .map { it.removePrefix("data:").trimStart() }
                .toList()
            if (dataLines.isEmpty()) continue

            try {
                val payload = JSONObject(dataLines.joinToString("\n"))
                events.add(payload)
            } catch (_: Exception) {
                // skip malformed JSON
            }
        }
        return events to rest
    }

    fun flushRemainder(buffer: String): List<JSONObject> {
        val trimmed = buffer.trim()
        if (trimmed.isEmpty()) return emptyList()
        val withTerminator = if (buffer.endsWith("\n\n") || buffer.endsWith("\r\n\r\n")) {
            buffer
        } else {
            "$buffer\n\n"
        }
        return parseChunk(withTerminator).first
    }
}
