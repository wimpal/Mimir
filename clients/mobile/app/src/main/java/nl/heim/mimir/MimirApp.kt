package nl.heim.mimir

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import nl.heim.mimir.data.SettingsRepository
import nl.heim.mimir.ui.chat.ChatScreen
import nl.heim.mimir.ui.chat.ChatViewModel
import nl.heim.mimir.ui.settings.SettingsScreen
import nl.heim.mimir.ui.settings.SettingsViewModel

object Routes {
    const val Settings = "settings"
    const val Chat = "chat"
}

@Composable
fun MimirApp(repository: SettingsRepository) {
    val navController = rememberNavController()
    val initialSettings = remember {
        runBlocking { repository.settingsFlow.first() }
    }
    val settings by repository.settingsFlow.collectAsStateWithLifecycle(
        initialValue = initialSettings,
    )
    val startDestination = remember(initialSettings) {
        if (initialSettings.isConfigured) Routes.Chat else Routes.Settings
    }

    NavHost(navController = navController, startDestination = startDestination) {
        composable(Routes.Settings) {
            val vm: SettingsViewModel = viewModel(
                factory = SettingsViewModel.Factory(repository),
            )
            SettingsScreen(
                viewModel = vm,
                onConfigured = {
                    navController.navigate(Routes.Chat) {
                        popUpTo(Routes.Settings) { inclusive = true }
                    }
                },
                showContinue = settings.isConfigured,
            )
        }
        composable(Routes.Chat) {
            val appContext = LocalContext.current.applicationContext
            val vm: ChatViewModel = viewModel(
                factory = ChatViewModel.Factory(repository, appContext),
            )
            ChatScreen(
                viewModel = vm,
                onOpenSettings = { navController.navigate(Routes.Settings) },
            )
        }
    }
}
