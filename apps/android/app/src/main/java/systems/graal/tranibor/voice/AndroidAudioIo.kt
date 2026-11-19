package systems.graal.tranibor.voice

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The real microphone and speaker. The capture uses the VOICE_COMMUNICATION source, which asks the platform for
 * its echo cancelling and noise suppression, and attaches the effects explicitly where a device offers them:
 * without echo cancelling the agent hears itself through the speaker and answers its own voice.
 * Not exercised on a device by the test suite (there is none): what is unit tested is the framing and the session.
 */
class AndroidAudioIo(override val sampleRate: Int = Pcm.DEFAULT_RATE, private val frameMillis: Int = 40) : AudioIo {
    private var record: AudioRecord? = null
    private var track: AudioTrack? = null
    private val effects = mutableListOf<android.media.audiofx.AudioEffect>()
    private val running = AtomicBoolean(false)
    private val playQueue = LinkedBlockingQueue<ByteArray>()
    private var readThread: Thread? = null
    private var writeThread: Thread? = null

    @SuppressLint("MissingPermission") // RECORD_AUDIO is requested by the screen before a session starts.
    override fun start(onFrame: (ByteArray) -> Unit) {
        if (!running.compareAndSet(false, true)) return
        val frame = Pcm.bytesFor(frameMillis, sampleRate)
        val inMin = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(inMin, frame * 4),
        )
        record = rec
        if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(rec.audioSessionId)?.also { it.enabled = true; effects += it }
        if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = true; effects += it }
        if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(rec.audioSessionId)?.also { it.enabled = true; effects += it }

        val outMin = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val out = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder().setSampleRate(sampleRate).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(outMin, Pcm.bytesFor(500, sampleRate)))
            .build()
        track = out
        out.play()
        rec.startRecording()

        readThread = Thread({
            val framer = Framer(frame)
            val buffer = ByteArray(frame)
            while (running.get()) {
                val n = rec.read(buffer, 0, buffer.size)
                if (n <= 0) continue
                framer.push(buffer, n).forEach(onFrame)
            }
        }, "tranibor-mic").also { it.start() }

        writeThread = Thread({
            while (running.get()) {
                val pcm = try {
                    playQueue.take()
                } catch (_: InterruptedException) {
                    break
                }
                if (pcm.isNotEmpty()) track?.write(pcm, 0, pcm.size)
            }
        }, "tranibor-speaker").also { it.start() }
    }

    override fun play(pcm: ByteArray) {
        if (running.get()) playQueue.offer(pcm)
    }

    override fun flush() {
        playQueue.clear()
        track?.let {
            runCatching {
                it.pause()
                it.flush()
                it.play()
            }
        }
    }

    override fun stop() {
        if (!running.compareAndSet(true, false)) return
        writeThread?.interrupt()
        runCatching { record?.stop() }
        effects.forEach { runCatching { it.release() } }
        effects.clear()
        runCatching { record?.release() }
        runCatching { track?.stop() }
        runCatching { track?.release() }
        record = null
        track = null
        playQueue.clear()
    }
}
