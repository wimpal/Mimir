package nl.heim.mimir.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import nl.heim.mimir.data.BrainApi
import nl.heim.mimir.data.BrainClientError
import nl.heim.mimir.data.BrainUrlSource
import nl.heim.mimir.data.SettingsRepository
import nl.heim.mimir.model.HealthInfo
import nl.heim.mimir.model.HealthState

data class SettingsUiState(
    val brainLanUrl: String = "",
    val brainAwayUrl: String = "",
    val authToken: String = "",
    val isSaving: Boolean = false,
    val health: HealthInfo? = null,
    val activeEndpointLabel: String? = null,
    val errorMessage: String? = null,
    val saveSuccess: Boolean = false,
)

class SettingsViewModel(
    private val repository: SettingsRepository,
) : ViewModel() {
    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            val settings = repository.settingsFlow.first()
            _uiState.update {
                it.copy(
                    brainLanUrl = settings.brainLanUrl,
                    brainAwayUrl = settings.brainAwayUrl,
                    authToken = settings.authToken,
                )
            }
            if (settings.isConfigured) {
                checkConnection(settings.brainLanUrl, settings.brainAwayUrl, settings.authToken)
            }
        }
    }

    fun onLanUrlChange(value: String) {
        _uiState.update { it.copy(brainLanUrl = value, saveSuccess = false, errorMessage = null) }
    }

    fun onAwayUrlChange(value: String) {
        _uiState.update { it.copy(brainAwayUrl = value, saveSuccess = false, errorMessage = null) }
    }

    fun onTokenChange(value: String) {
        _uiState.update { it.copy(authToken = value, saveSuccess = false, errorMessage = null) }
    }

    fun save() {
        val lan = _uiState.value.brainLanUrl.trim()
        val away = _uiState.value.brainAwayUrl.trim()
        val token = _uiState.value.authToken.trim()
        if (lan.isEmpty() && away.isEmpty()) {
            _uiState.update { it.copy(errorMessage = "Enter a home URL, away URL, or both.") }
            return
        }
        if (token.isEmpty()) {
            _uiState.update { it.copy(errorMessage = "Bearer token is required.") }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isSaving = true, errorMessage = null, saveSuccess = false) }
            try {
                val normalizedLan = if (lan.isEmpty()) "" else BrainApi.normalizeBrainUrl(lan)
                val normalizedAway = if (away.isEmpty()) "" else BrainApi.normalizeBrainUrl(away)
                repository.saveSettings(normalizedLan, normalizedAway, token)
                checkConnection(normalizedLan, normalizedAway, token)
                _uiState.update { it.copy(isSaving = false, saveSuccess = true) }
            } catch (e: IllegalArgumentException) {
                _uiState.update {
                    it.copy(isSaving = false, errorMessage = e.message ?: "Invalid URL.")
                }
            } catch (e: BrainClientError) {
                _uiState.update {
                    it.copy(isSaving = false, errorMessage = e.message, health = offlineHealth())
                }
            }
        }
    }

    private suspend fun checkConnection(lan: String, away: String, token: String) {
        try {
            val settings = repository.settingsFlow.first().copy(
                brainLanUrl = lan,
                brainAwayUrl = away,
                authToken = token,
            )
            val endpoint = repository.resolveBrainEndpoint(settings, forceRefresh = true)
            val api = BrainApi(endpoint.url, token)
            val health = api.health()
            _uiState.update {
                it.copy(
                    health = health,
                    errorMessage = null,
                    activeEndpointLabel = endpointLabel(endpoint.url, endpoint.source),
                )
            }
            if (health.state != HealthState.Offline) {
                api.listConversations(limit = 1)
            }
        } catch (e: BrainClientError) {
            _uiState.update {
                it.copy(
                    health = offlineHealth(),
                    errorMessage = e.message,
                    activeEndpointLabel = null,
                )
            }
        }
    }

    private fun endpointLabel(url: String, source: BrainUrlSource): String =
        when (source) {
            BrainUrlSource.Lan -> "Using home (LAN): $url"
            BrainUrlSource.Away -> "Using away (Tailscale): $url"
        }

    private fun offlineHealth() = HealthInfo(
        state = HealthState.Offline,
        status = "offline",
        detail = "unreachable",
    )

    class Factory(
        private val repository: SettingsRepository,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SettingsViewModel(repository) as T
        }
    }
}
