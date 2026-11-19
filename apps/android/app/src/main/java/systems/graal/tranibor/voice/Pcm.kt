package systems.graal.tranibor.voice

import java.util.Base64
import kotlin.math.abs
import kotlin.math.sqrt

/** PCM16, little-endian, mono: the only audio of a voice session (ADR 0080 section 10), 24 kHz by default. */
object Pcm {
    const val DEFAULT_RATE = 24_000
    const val BYTES_PER_SAMPLE = 2

    /** Bytes of [millis] of audio at [rate]. An odd count is never produced: a sample is never cut in half. */
    fun bytesFor(millis: Int, rate: Int = DEFAULT_RATE): Int = rate * millis / 1000 * BYTES_PER_SAMPLE

    fun encode(pcm: ByteArray): String = Base64.getEncoder().encodeToString(pcm)

    /** The bytes of a base64 audio delta, without a trailing odd byte; `null` when it is not base64. */
    fun decode(text: String): ByteArray? {
        val raw = try {
            Base64.getDecoder().decode(text)
        } catch (_: IllegalArgumentException) {
            return null
        }
        return if (raw.size % 2 == 0) raw else raw.copyOf(raw.size - 1)
    }

    /** Root mean square of the samples, 0.0 (silence) to 1.0 (full scale): what the level meter draws. */
    fun level(pcm: ByteArray): Float {
        if (pcm.size < 2) return 0f
        var sum = 0.0
        var n = 0
        var i = 0
        while (i + 1 < pcm.size) {
            val sample = ((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xFF)).toShort().toInt()
            sum += sample.toDouble() * sample
            n++
            i += 2
        }
        return (sqrt(sum / n) / 32768.0).toFloat().coerceIn(0f, 1f)
    }

    /** The peak sample, 0 to 32768: used by tests to tell speech from silence. */
    fun peak(pcm: ByteArray): Int {
        var peak = 0
        var i = 0
        while (i + 1 < pcm.size) {
            peak = maxOf(peak, abs(((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xFF)).toShort().toInt()))
            i += 2
        }
        return peak
    }
}

/**
 * Cuts what the microphone returns (any size, whatever the driver chose) into frames of exactly [frameBytes],
 * so every `input_audio_buffer.append` has the same weight. The rest waits for the next read.
 */
class Framer(private val frameBytes: Int) {
    init {
        require(frameBytes > 0 && frameBytes % Pcm.BYTES_PER_SAMPLE == 0) { "a frame holds whole samples" }
    }

    private var rest = ByteArray(0)

    fun push(chunk: ByteArray, length: Int = chunk.size): List<ByteArray> {
        val all = rest + chunk.copyOf(length)
        val frames = ArrayList<ByteArray>(all.size / frameBytes)
        var at = 0
        while (all.size - at >= frameBytes) {
            frames += all.copyOfRange(at, at + frameBytes)
            at += frameBytes
        }
        rest = all.copyOfRange(at, all.size)
        return frames
    }

    /** What is held and not yet a frame, in bytes. */
    val pending: Int get() = rest.size
}
