package nl.heim.mimir.data

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream

class AudioRecorder {
    companion object {
        const val SAMPLE_RATE = 16_000
        const val MAX_SECONDS = 30
        const val MIN_SECONDS = 0.35
    }

    private var audioRecord: AudioRecord? = null
    private val pcmBuffer = ByteArrayOutputStream()

    @Volatile
    var isRecording: Boolean = false
        private set

    fun begin(): Boolean {
        cancel()
        pcmBuffer.reset()
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) return false
        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            minBuf * 2,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return false
        }
        audioRecord = record
        isRecording = true
        record.startRecording()
        return true
    }

    /** Block until [isRecording] becomes false or max duration reached. Call on IO thread. */
    fun captureLoop() {
        val record = audioRecord ?: return
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(1024)
        val chunk = ByteArray(minBuf)
        val maxBytes = SAMPLE_RATE * 2 * MAX_SECONDS
        while (isRecording && pcmBuffer.size() < maxBytes) {
            val read = record.read(chunk, 0, chunk.size)
            when {
                read > 0 -> pcmBuffer.write(chunk, 0, read)
                read < 0 -> break
            }
        }
    }

    fun finish(): ByteArray? {
        isRecording = false
        val record = audioRecord
        audioRecord = null
        if (record != null) {
            try {
                record.stop()
            } catch (_: IllegalStateException) {
            }
            record.release()
        }
        val pcm = pcmBuffer.toByteArray()
        pcmBuffer.reset()
        val minBytes = (SAMPLE_RATE * 2 * MIN_SECONDS).toInt()
        if (pcm.size < minBytes) return null
        return WavEncoder.encodePcm16Mono(pcm, SAMPLE_RATE)
    }

    fun cancel() {
        isRecording = false
        audioRecord?.let { record ->
            try {
                record.stop()
            } catch (_: IllegalStateException) {
            }
            record.release()
        }
        audioRecord = null
        pcmBuffer.reset()
    }
}
