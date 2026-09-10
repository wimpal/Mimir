package nl.heim.mimir.data

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Build a RIFF WAV from 16-bit mono PCM samples. */
object WavEncoder {
    fun encodePcm16Mono(pcm: ByteArray, sampleRate: Int): ByteArray {
        val channels = 1
        val bitsPerSample = 16
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = (channels * bitsPerSample / 8).toShort()
        val dataSize = pcm.size
        val chunkSize = 36 + dataSize

        val out = ByteArrayOutputStream(44 + dataSize)
        out.write("RIFF".toByteArray())
        out.write(intLe(chunkSize))
        out.write("WAVE".toByteArray())
        out.write("fmt ".toByteArray())
        out.write(intLe(16))
        out.write(shortLe(1)) // PCM
        out.write(shortLe(channels.toShort()))
        out.write(intLe(sampleRate))
        out.write(intLe(byteRate))
        out.write(shortLe(blockAlign))
        out.write(shortLe(bitsPerSample.toShort()))
        out.write("data".toByteArray())
        out.write(intLe(dataSize))
        out.write(pcm)
        return out.toByteArray()
    }

    private fun intLe(value: Int): ByteArray =
        ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(value).array()

    private fun shortLe(value: Short): ByteArray =
        ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(value).array()

    private fun shortLe(value: Int): ByteArray = shortLe(value.toShort())
}
