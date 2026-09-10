package nl.heim.mimir.data

import org.junit.Assert.assertEquals
import org.junit.Test

class TurnLocaleTest {
    @Test
    fun metaLocaleOverridesFallback() {
        assertEquals("nl", TurnLocale.forTts("nl", "en"))
        assertEquals("en", TurnLocale.forTts("en", "nl"))
        assertEquals("nl", TurnLocale.forTts("nl_NL", "en"))
        assertEquals("en", TurnLocale.forTts("en-US", "nl"))
    }

    @Test
    fun missingMetaKeepsFallback() {
        assertEquals("nl", TurnLocale.forTts(null, "nl"))
        assertEquals("en", TurnLocale.forTts("", "en"))
        assertEquals("nl", TurnLocale.forTts("xx", "nl"))
    }
}
