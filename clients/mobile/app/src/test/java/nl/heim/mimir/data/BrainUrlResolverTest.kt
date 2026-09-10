package nl.heim.mimir.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BrainUrlResolverTest {
    @Test
    fun migrateLegacyUrl_lanIpGoesToLan() {
        val (lan, away) = BrainUrlResolver.migrateLegacyUrl("http://192.168.0.42:8000")
        assertEquals("http://192.168.0.42:8000", lan)
        assertEquals("", away)
    }

    @Test
    fun migrateLegacyUrl_tailscaleIpGoesToAway() {
        val (lan, away) = BrainUrlResolver.migrateLegacyUrl("http://100.64.0.5:8000")
        assertEquals("", lan)
        assertEquals("http://100.64.0.5:8000", away)
    }

    @Test
    fun migrateLegacyUrl_magicDnsGoesToAway() {
        val (lan, away) = BrainUrlResolver.migrateLegacyUrl(
            "http://desktop-8nhdeb5.taildc5ad7.ts.net:8000",
        )
        assertEquals("", lan)
        assertEquals("http://desktop-8nhdeb5.taildc5ad7.ts.net:8000", away)
    }

    @Test
    fun looksLikeAwayUrl_detectsTailscaleHosts() {
        assertTrue(BrainUrlResolver.looksLikeAwayUrl("http://100.1.2.3:8000"))
        assertTrue(BrainUrlResolver.looksLikeAwayUrl("http://pc.tail1234.ts.net:8000"))
        assertFalse(BrainUrlResolver.looksLikeAwayUrl("http://192.168.0.42:8000"))
    }
}
