package systems.graal.tranibor.voice

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import systems.graal.tranibor.net.Instance
import java.util.concurrent.TimeUnit

/** Which voice agent to talk to, in which project. */
data class VoiceConfig(
    val org: String,
    val slug: String,
    val project: String,
    val title: String,
    val sampleRate: Int = Pcm.DEFAULT_RATE,
    val maxUsd: Double? = null,
)

/**
 * Holds the one voice session of the app. It lives here, not in a screen, so that it survives the screen being
 * left or turned off: [VoiceService] keeps the process alive and the microphone legitimately open (ADR 0193).
 */
class VoiceController(
    private val context: Context,
    private val origin: () -> String?,
    private val token: () -> String?,
) {
    private val _state = MutableStateFlow<VoiceState?>(null)

    /** The live session's state, or `null` when no session is open. */
    val state: StateFlow<VoiceState?> = _state.asStateFlow()

    @Volatile var config: VoiceConfig? = null
        private set

    private var session: VoiceSession? = null
    private var router: AudioRouter? = null
    private var scope: CoroutineScope? = null
    private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(20, TimeUnit.SECONDS).build()

    /** Starts the foreground service, which calls [begin] once it is in the foreground. */
    fun request(config: VoiceConfig) {
        if (session != null) return
        this.config = config
        _state.value = VoiceState()
        ContextCompat.startForegroundService(context, Intent(context, VoiceService::class.java))
    }

    /** Called by the service, in the foreground: opens the audio and the socket. */
    fun begin() {
        val c = config ?: return
        val base = origin() ?: return finishAlone()
        if (session != null) return
        val url = buildString {
            append(Instance.websocket(base, "/orgs/${c.org}/agents/${c.slug}/realtime"))
            append("?project=").append(c.project)
            c.maxUsd?.let { append("&max_usd=").append(it) }
        }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default).also { this.scope = it }
        val routing = AudioRouter(context).also { it.acquire(); router = it }
        val live = VoiceSession(url, token, http, AndroidAudioIo(c.sampleRate), scope)
        session = live
        scope.launch { live.state.collect { _state.value = it } }
        live.start()
        // Routing is released when the session ends, wherever it ended from.
        scope.launch { live.state.collect { if (it.phase == Phase.Ended) { routing.release(); router = null } } }
    }

    fun stop() {
        session?.stop()
    }

    fun mute(on: Boolean) { session?.mute(on) }

    fun interrupt() { session?.interrupt() }

    fun sendText(text: String) { session?.sendText(text) }

    /** Forgets an ended session, so the screen goes back to its start. */
    fun dismiss() {
        if (_state.value?.phase != Phase.Ended && _state.value != null) return
        scope?.cancel()
        scope = null
        session = null
        config = null
        _state.value = null
    }

    private fun finishAlone() {
        _state.value = VoiceState(
            phase = Phase.Ended,
            notice = CloseNotice(EndKind.Refused, "You are not signed in."),
        )
    }
}
