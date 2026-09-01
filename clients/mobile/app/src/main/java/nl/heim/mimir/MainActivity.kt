package nl.heim.mimir

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import nl.heim.mimir.data.SettingsRepository

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val repository = SettingsRepository(applicationContext)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = androidx.compose.ui.graphics.Color(0xFF4A7C59),
                    primaryContainer = androidx.compose.ui.graphics.Color(0xFF2A4A35),
                    surface = androidx.compose.ui.graphics.Color(0xFF0D0F0C),
                    surfaceVariant = androidx.compose.ui.graphics.Color(0xFF1A2218),
                    background = androidx.compose.ui.graphics.Color(0xFF0D0F0C),
                ),
            ) {
                MimirApp(repository = repository)
            }
        }
    }
}
