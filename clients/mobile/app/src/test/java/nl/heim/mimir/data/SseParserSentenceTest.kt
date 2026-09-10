package nl.heim.mimir.data

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class SseParserSentenceTest {
    @Test
    fun parseSentenceEvent() {
        val chunk = """data: {"type":"sentence","index":1,"text":"Second one."}

"""
        val (events, _) = SseParser.parseChunk(chunk)
        assertEquals(1, events.size)
        assertEquals("sentence", events[0].getString("type"))
        assertEquals(1, events[0].getInt("index"))
        assertEquals("Second one.", events[0].getString("text"))
    }
}
