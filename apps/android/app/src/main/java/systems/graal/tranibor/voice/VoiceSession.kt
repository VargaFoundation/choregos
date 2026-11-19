package systems.graal.tranibor.voice

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** The microphone and the speaker, behind an interface so the session can be driven without a device. */
interface AudioIo {
    val sampleRate: Int

    /** Starts the capture: [onFrame] gets fixed-size PCM16 frames until [stop]. */
    fun start(onFrame: (ByteArray) -> Unit)

    /** Queues PCM16 for the speaker. */
    fun play(pcm: ByteArray)

    /** Drops what is queued for the speaker, now (the barge-in). */
    fun flush()

    fun stop()
}

enum class Phase { Connecting, Listening, Speaking, Reconnecting, Ended }

enum class Role { Person, Agent }

data class TranscriptLine(val id: Int, val role: Role, val text: String, val final: Boolean)

data class VoiceState(
    val phase: Phase = Phase.Connecting,
    val transcript: List<TranscriptLine> = emptyList(),
    val micLevel: Float = 0f,
    val muted: Boolean = false,
    val notice: CloseNotice? = null,
)

/**
 * One voice session against the relay (ADR 0080 section 10): a WebSocket of OpenAI Realtime events, PCM16 in both
 * directions, the server's own voice activity detection (the phone never sends `session.update`).
 *
 * The state machine: Connecting, then Listening and Speaking as the model and the person take turns, Reconnecting
 * when the socket drops without a close frame (the relay holds the session for [resumeWindowMs] and replays what
 * was missed after the last `seq` read), and Ended for good with a [CloseNotice] saying why.
 */
class VoiceSession(
    private val url: String,
    private val token: () -> String?,
    private val http: OkHttpClient,
    private val io: AudioIo,
    private val scope: CoroutineScope,
    private val resumeWindowMs: Long = 30_000,
    private val backoffMs: List<Long> = listOf(500, 1_000, 2_000, 4_000),
    private val now: () -> Long = System::currentTimeMillis,
) {
    private val lock = Any()
    private val _state = MutableStateFlow(VoiceState())
    val state: StateFlow<VoiceState> = _state.asStateFlow()

    private var socket: WebSocket? = null
    private var generation = 0
    private var sessionId: String? = null
    private var lastSeq = 0
    private var everOpened = false
    private var droppedAt = 0L
    private var attempt = 0
    private var retry: Job? = null
    private var ended = false
    private var capturing = false
    private var nextLine = 0
    private var openAgentLine: Int? = null

    fun start() { synchronized(lock) { connect() } }

    /** The person hangs up. */
    fun stop() {
        synchronized(lock) {
            if (ended) return@synchronized
            socket?.close(1000, "hang up")
            finish(CloseNotice(EndKind.Normal, "You ended the session."))
        }
    }

    fun mute(on: Boolean) = _state.update { it.copy(muted = on) }

    /** Cuts the agent off by hand: the model stops talking and what was queued for the speaker is dropped. */
    fun interrupt() {
        synchronized(lock) {
            io.flush()
            socket?.send(RealtimeCodec.cancel())
            _state.update { if (it.phase == Phase.Speaking) it.copy(phase = Phase.Listening) else it }
        }
    }

    fun sendText(text: String) {
        synchronized(lock) {
            val clean = text.trim()
            if (clean.isEmpty() || ended) return@synchronized
            RealtimeCodec.userText(clean).forEach { socket?.send(it) }
            addLine(Role.Person, clean, final = true)
        }
    }

    // ───────────── the socket ─────────────

    private fun connect() {
        val resuming = sessionId != null
        val target = if (resuming) "$url&session=$sessionId&after=$lastSeq" else url
        val request = Request.Builder().url(target).apply { token()?.let { header("Authorization", "Bearer $it") } }.build()
        val mine = ++generation
        socket = http.newWebSocket(request, Listener(mine))
    }

    private inner class Listener(private val mine: Int) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            synchronized(lock) {
                if (mine != generation || ended) return@synchronized
                everOpened = true
                droppedAt = 0
                attempt = 0
                _state.update { it.copy(phase = if (it.phase == Phase.Speaking) Phase.Speaking else Phase.Listening) }
                if (!capturing) {
                    capturing = true
                    io.start(::onFrame)
                }
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            synchronized(lock) {
                if (mine != generation || ended) return@synchronized
                RealtimeCodec.parse(text)?.let(::handle)
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            synchronized(lock) { if (mine == generation) finish(CloseNotice.of(code, reason)) }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            synchronized(lock) {
                if (mine == generation) finish(CloseNotice.of(code, reason))
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            synchronized(lock) {
                if (mine != generation || ended) return@synchronized
                val status = response?.code
                when {
                    status == 401 -> finish(CloseNotice(EndKind.Refused, "Your sign-in is no longer valid. Sign in again."))
                    status != null && status in 400..499 ->
                        finish(CloseNotice(EndKind.Refused, "The instance refused the voice session (HTTP $status)."))
                    sessionId == null -> finish(if (everOpened) CloseNotice.lostConnection else CloseNotice.unreachable)
                    else -> reconnectOrGiveUp()
                }
            }
        }
    }

    private fun reconnectOrGiveUp() {
        if (droppedAt == 0L) droppedAt = now()
        if (now() - droppedAt >= resumeWindowMs) {
            finish(CloseNotice.lostConnection)
            return
        }
        _state.update { it.copy(phase = Phase.Reconnecting) }
        io.flush()
        val wait = backoffMs[attempt.coerceAtMost(backoffMs.size - 1)]
        attempt++
        retry?.cancel()
        retry = scope.launch {
            delay(wait)
            synchronized(lock) { if (!ended) connect() }
        }
    }

    // ───────────── what the relay says ─────────────

    private fun handle(event: Inbound) {
        event.seq?.let { if (it > lastSeq) lastSeq = it }
        when (event) {
            is Inbound.Session -> sessionId = event.id
            is Inbound.AudioDelta -> {
                io.play(event.pcm)
                _state.update { it.copy(phase = Phase.Speaking) }
            }
            is Inbound.AudioDone, is Inbound.ResponseDone ->
                _state.update { if (it.phase == Phase.Speaking) it.copy(phase = Phase.Listening) else it }
            // Barge-in: the person speaks over the agent. What is queued for the speaker is dropped at once;
            // the server's VAD stops the model's response by itself.
            is Inbound.SpeechStarted -> {
                io.flush()
                openAgentLine = null
                _state.update { it.copy(phase = Phase.Listening) }
            }
            is Inbound.UserTranscript -> if (event.text.isNotEmpty()) addLine(Role.Person, event.text, final = true)
            is Inbound.AssistantTranscriptDelta -> appendAgent(event.text)
            is Inbound.AssistantTranscriptDone -> finishAgent(event.text)
            is Inbound.Error -> if (event.code != null || event.message.isNotEmpty()) {
                _state.update { it.copy(notice = CloseNotice(EndKind.Refused, event.message)) }
            }
            is Inbound.ResponseCreated, is Inbound.SpeechStopped, is Inbound.Ignored -> Unit
        }
    }

    private fun addLine(role: Role, text: String, final: Boolean): Int {
        val id = nextLine++
        _state.update { it.copy(transcript = it.transcript + TranscriptLine(id, role, text, final)) }
        return id
    }

    private fun appendAgent(delta: String) {
        val id = openAgentLine
        if (id == null) {
            openAgentLine = addLine(Role.Agent, delta, final = false)
        } else {
            _state.update { s -> s.copy(transcript = s.transcript.map { if (it.id == id) it.copy(text = it.text + delta) else it }) }
        }
    }

    private fun finishAgent(full: String) {
        val id = openAgentLine
        openAgentLine = null
        if (id == null) {
            if (full.isNotEmpty()) addLine(Role.Agent, full, final = true)
        } else {
            _state.update { s -> s.copy(transcript = s.transcript.map { if (it.id == id) it.copy(text = full.ifEmpty { it.text }, final = true) else it }) }
        }
    }

    // ───────────── the microphone ─────────────

    private fun onFrame(frame: ByteArray) {
        val s = _state.value
        _state.update { it.copy(micLevel = if (s.muted) 0f else Pcm.level(frame)) }
        if (s.muted || (s.phase != Phase.Listening && s.phase != Phase.Speaking)) return
        socket?.send(RealtimeCodec.append(frame))
    }

    private fun finish(notice: CloseNotice) {
        if (ended) return
        ended = true
        retry?.cancel()
        io.stop()
        _state.update { it.copy(phase = Phase.Ended, notice = notice, micLevel = 0f) }
    }
}
