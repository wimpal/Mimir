package nl.heim.mimir.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "mimir_settings")

data class AppSettings(
    val brainLanUrl: String = "",
    val brainAwayUrl: String = "",
    val authToken: String = "",
    val conversationId: String? = null,
) {
    /** @deprecated use [brainLanUrl] / [brainAwayUrl]; kept for migration reads. */
    val brainBaseUrl: String
        get() = brainLanUrl.ifBlank { brainAwayUrl }

    val isConfigured: Boolean
        get() = authToken.isNotBlank() && (brainLanUrl.isNotBlank() || brainAwayUrl.isNotBlank())
}

class SettingsRepository(private val context: Context) {
    private val legacyUrlKey = stringPreferencesKey("brain_base_url")
    private val lanUrlKey = stringPreferencesKey("brain_lan_url")
    private val awayUrlKey = stringPreferencesKey("brain_away_url")
    private val conversationKey = stringPreferencesKey("conversation_id")

    private val urlResolver = BrainUrlResolver()

    private val securePrefs by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "mimir_secure_prefs",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    val settingsFlow: Flow<AppSettings> = context.dataStore.data.map { prefs ->
        val lanStored = prefs[lanUrlKey].orEmpty()
        val awayStored = prefs[awayUrlKey].orEmpty()
        val legacy = prefs[legacyUrlKey].orEmpty()

        val (lan, away) = when {
            lanStored.isNotEmpty() || awayStored.isNotEmpty() -> lanStored to awayStored
            legacy.isNotEmpty() -> BrainUrlResolver.migrateLegacyUrl(legacy)
            else -> "" to ""
        }

        AppSettings(
            brainLanUrl = lan,
            brainAwayUrl = away,
            authToken = securePrefs.getString(KEY_TOKEN, "").orEmpty(),
            conversationId = prefs[conversationKey]?.ifBlank { null },
        )
    }

    suspend fun saveBrainUrls(lanUrl: String, awayUrl: String) {
        val lan = lanUrl.trim()
        val away = awayUrl.trim()
        context.dataStore.edit { prefs ->
            if (lan.isEmpty()) prefs.remove(lanUrlKey) else prefs[lanUrlKey] = lan
            if (away.isEmpty()) prefs.remove(awayUrlKey) else prefs[awayUrlKey] = away
            prefs.remove(legacyUrlKey)
        }
        urlResolver.invalidateCache()
    }

    suspend fun saveAuthToken(token: String) {
        securePrefs.edit().putString(KEY_TOKEN, token.trim()).apply()
        urlResolver.invalidateCache()
    }

    suspend fun saveConversationId(conversationId: String?) {
        context.dataStore.edit { prefs ->
            if (conversationId.isNullOrBlank()) {
                prefs.remove(conversationKey)
            } else {
                prefs[conversationKey] = conversationId.trim()
            }
        }
    }

    suspend fun saveSettings(lanUrl: String, awayUrl: String, token: String) {
        saveBrainUrls(lanUrl, awayUrl)
        saveAuthToken(token)
    }

    suspend fun resolveBrainEndpoint(
        settings: AppSettings,
        forceRefresh: Boolean = false,
    ): ResolvedBrainEndpoint = urlResolver.resolve(settings, forceRefresh)

    suspend fun createBrainApi(
        settings: AppSettings,
        forceRefresh: Boolean = false,
    ): BrainApi {
        val endpoint = resolveBrainEndpoint(settings, forceRefresh)
        return BrainApi(endpoint.url, settings.authToken)
    }

    companion object {
        private const val KEY_TOKEN = "auth_token"
    }
}
