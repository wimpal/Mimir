package nl.heim.mimir.data

import android.content.Context
import android.media.MediaPlayer
import java.io.File

class AudioPlayer(private val context: Context) {
    private var player: MediaPlayer? = null
    private var tempFile: File? = null

    val isPlaying: Boolean
        get() = player?.isPlaying == true

    fun play(wav: ByteArray, onComplete: () -> Unit) {
        stop()
        val file = File.createTempFile("mimir_tts_", ".wav", context.cacheDir)
        file.writeBytes(wav)
        tempFile = file
        val mp = MediaPlayer()
        player = mp
        mp.setDataSource(file.absolutePath)
        mp.setOnCompletionListener {
            stop()
            onComplete()
        }
        mp.setOnErrorListener { _, _, _ ->
            stop()
            onComplete()
            true
        }
        mp.prepare()
        mp.start()
    }

    fun stop() {
        player?.let { mp ->
            try {
                if (mp.isPlaying) mp.stop()
            } catch (_: IllegalStateException) {
            }
            mp.release()
        }
        player = null
        tempFile?.delete()
        tempFile = null
    }
}
