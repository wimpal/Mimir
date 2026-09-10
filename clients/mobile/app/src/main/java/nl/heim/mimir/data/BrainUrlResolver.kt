package nl.heim.mimir.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import nl.heim.mimir.model.HealthState
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URI
import java.util.concurrent.TimeUnit

enum class BrainUrlSource {
    Lan,
    Away,
}

data class ResolvedBrainEndpoint(
    val url: String,
    val source: BrainUrlSource,
)

/**
 * Picks brain base URL: try [AppSettings.brainLanUrl] first, then [AppSettings.brainAwayUrl].
 * Caches the result briefly to avoid probing LAN on every chat turn.
 */
class BrainUrlResolver(
    private val lanProbeTimeoutS: Long = 2L,
    private val cacheTtlMs: Long = 30_000L,
) {
    private var cache: CacheEntry? = null

    private data class CacheEntry(
        val settingsKey: String,
        val endpoint: ResolvedBrainEndpoint,
        val expiresAtMs: Long,
    )

    suspend fun resolve(settings: AppSettings, forceRefresh: Boolean = false): ResolvedBrainEndpoint {
        val key = settings.resolverCacheKey()
        val now = System.currentTimeMillis()
        if (!forceRefresh) {
            cache?.let { entry ->
                if (entry.settingsKey == key && now < entry.expiresAtMs) {
                    return entry.endpoint
                }
            }
        }

        val endpoint = selectEndpoint(settings)
        cache = CacheEntry(key, endpoint, now + cacheTtlMs)
        return endpoint
    }

    fun invalidateCache() {
        cache = null
    }

    private suspend fun selectEndpoint(settings: AppSettings): ResolvedBrainEndpoint {
        val lan = settings.brainLanUrl.trim()
        val away = settings.brainAwayUrl.trim()
        require(lan.isNotEmpty() || away.isNotEmpty()) { "No brain URL configured" }

        if (lan.isNotEmpty() && probeLanReachable(lan)) {
            return ResolvedBrainEndpoint(
                url = BrainApi.normalizeBrainUrl(lan),
                source = BrainUrlSource.Lan,
            )
        }
        if (away.isNotEmpty()) {
            return ResolvedBrainEndpoint(
                url = BrainApi.normalizeBrainUrl(away),
                source = BrainUrlSource.Away,
            )
        }
        return ResolvedBrainEndpoint(
            url = BrainApi.normalizeBrainUrl(lan),
            source = BrainUrlSource.Lan,
        )
    }

    private suspend fun probeLanReachable(lanUrl: String): Boolean = withContext(Dispatchers.IO) {
        val normalized = try {
            BrainApi.normalizeBrainUrl(lanUrl)
        } catch (_: IllegalArgumentException) {
            return@withContext false
        }
        val client = OkHttpClient.Builder()
            .connectTimeout(lanProbeTimeoutS, TimeUnit.SECONDS)
            .readTimeout(lanProbeTimeoutS, TimeUnit.SECONDS)
            .writeTimeout(lanProbeTimeoutS, TimeUnit.SECONDS)
            .callTimeout(lanProbeTimeoutS * 2, TimeUnit.SECONDS)
            .build()
        val request = Request.Builder()
            .url("$normalized/health")
            .header("Accept", "application/json")
            .get()
            .build()
        try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext false
                val bodyText = response.body?.string().orEmpty()
                val status = try {
                    JSONObject(bodyText).optString("status", "")
                } catch (_: Exception) {
                    return@withContext false
                }
                status == "ok"
            }
        } catch (_: Exception) {
            false
        }
    }

    companion object {
        fun migrateLegacyUrl(url: String): Pair<String, String> {
            val trimmed = url.trim()
            if (trimmed.isEmpty()) return "" to ""
            return if (looksLikeAwayUrl(trimmed)) {
                "" to trimmed
            } else {
                trimmed to ""
            }
        }

        fun looksLikeAwayUrl(url: String): Boolean {
            return try {
                val host = URI(url.trim()).host?.lowercase().orEmpty()
                host.endsWith(".ts.net") || host.startsWith("100.")
            } catch (_: Exception) {
                false
            }
        }
    }
}

private fun AppSettings.resolverCacheKey(): String =
    "${brainLanUrl}|${brainAwayUrl}|${authToken.isNotEmpty()}"
