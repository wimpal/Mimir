package nl.heim.mimir.data

import java.util.TreeMap

/**
 * Ordered WAV playback for sentence-streamed TTS (T-029).
 * Sentence N may be synthesized while N-1 plays.
 */
class AudioQueue(private val player: AudioPlayer) {
    private val lock = Any()
    private var nextIndex = 0
    private val pending = TreeMap<Int, ByteArray>()
    private var playing = false
    private var cancelled = false
    private val idleCallbacks = mutableListOf<() -> Unit>()
    private var onFirstPlayback: (() -> Unit)? = null
    private var firstPlaybackFired = false

    fun setOnFirstPlayback(callback: () -> Unit) {
        synchronized(lock) {
            onFirstPlayback = callback
        }
    }

    fun cancel() {
        synchronized(lock) {
            cancelled = true
            pending.clear()
            idleCallbacks.clear()
            onFirstPlayback = null
        }
        player.stop()
        synchronized(lock) {
            playing = false
        }
    }

    fun enqueue(index: Int, wav: ByteArray) {
        synchronized(lock) {
            if (cancelled) return
            pending[index] = wav
            maybePlayNextLocked()
        }
    }

    fun whenIdle(callback: () -> Unit) {
        synchronized(lock) {
            if (cancelled || (!playing && pending.isEmpty())) {
                callback()
            } else {
                idleCallbacks.add(callback)
            }
        }
    }

    private fun maybePlayNextLocked() {
        if (playing || cancelled) return
        val wav = pending.remove(nextIndex) ?: return
        playing = true
        if (!firstPlaybackFired) {
            firstPlaybackFired = true
            onFirstPlayback?.invoke()
        }
        player.play(wav) {
            synchronized(lock) {
                playing = false
                nextIndex++
                if (cancelled) {
                    idleCallbacks.clear()
                    return@synchronized
                }
                if (pending.containsKey(nextIndex)) {
                    maybePlayNextLocked()
                } else if (!playing && pending.isEmpty()) {
                    val callbacks = idleCallbacks.toList()
                    idleCallbacks.clear()
                    callbacks.forEach { it() }
                }
            }
        }
    }
}
