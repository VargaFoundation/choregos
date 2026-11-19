package systems.graal.tranibor.voice

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

/** The session state machine against a relay that speaks its protocol (a fake WebSocket server, no device). */
class VoiceSessionTest {
    private class FakeIo : AudioIo {
        override val sampleRate = 24_000
        val played = CopyOnWriteArrayList<ByteArray>()
        val flushes = AtomicInteger()
        val stopped = AtomicInteger()
        @Volatile var onFrame: ((ByteArray) -> Unit)? = null
        override fun start(onFrame: (ByteArray) -> Unit) { this.onFrame = onFrame }
        override fun play(pcm: ByteArray) { played += pcm }
        override fun flush() { flushes.incrementAndGet() }
        override fun stop() { stopped.incrementAndGet() }
    }

    private lateinit var server: MockWebServer
    private val scope = CoroutineScope(Dispatchers.Default)
    private val received = CopyOnWriteArrayList<String>()
    private val serverSockets = CopyOnWriteArrayList<WebSocket>()

    /** The phone's raw sockets, so a test can cut the link without any close frame, as a tunnel or a lift does. */
    private val rawSockets = CopyOnWriteArrayList<java.net.Socket>()
    private val recording = object : javax.net.SocketFactory() {
        private val real = javax.net.SocketFactory.getDefault()
        private fun <T : java.net.Socket> T.kept(): T = also { rawSockets += it }
        override fun createSocket() = real.createSocket().kept()
        override fun createSocket(host: String?, port: Int) = real.createSocket(host, port).kept()
        override fun createSocket(host: String?, port: Int, local: java.net.InetAddress?, localPort: Int) = real.createSocket(host, port, local, localPort).kept()
        override fun createSocket(host: java.net.InetAddress?, port: Int) = real.createSocket(host, port).kept()
        override fun createSocket(host: java.net.InetAddress?, port: Int, local: java.net.InetAddress?, localPort: Int) = real.createSocket(host, port, local, localPort).kept()
    }

    @Before fun up() { server = MockWebServer().also { it.start() } }

    @After fun down() { scope.cancel(); server.close() }

    private fun relay(onOpen: (WebSocket) -> Unit = {}) = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { serverSockets += webSocket; onOpen(webSocket) }
        override fun onMessage(webSocket: WebSocket, text: String) { received += text }
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
    }

    private fun upgrade(onOpen: (WebSocket) -> Unit = {}) = server.enqueue(MockResponse.Builder().webSocketUpgrade(relay(onOpen)).build())

    private fun session(io: FakeIo, resumeWindowMs: Long = 30_000) = VoiceSession(
        url = server.url("/api/v1/orgs/acme/agents/bot/realtime?project=p").toString(),
        token = { "chs_secret" },
        http = OkHttpClient.Builder().socketFactory(recording).build(),
        io = io,
        scope = scope,
        resumeWindowMs = resumeWindowMs,
        backoffMs = listOf(20),
    )

    private fun await(what: String, cond: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!cond()) {
            check(System.currentTimeMillis() < until) { "timed out waiting for: $what" }
            Thread.sleep(10)
        }
    }

    private val audio = Pcm.encode(ByteArray(960) { 5 })

    @Test fun `the session opens with the token, listens, and speaks what the agent says`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""") ; it.send("""{"type":"response.output_audio.delta","delta":"$audio","seq":2}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("audio played") { io.played.isNotEmpty() }
        assertEquals(Phase.Speaking, s.state.value.phase)
        assertEquals(960, io.played.first().size)
        val request = server.takeRequest()
        assertEquals("Bearer chs_secret", request.headers["Authorization"])
        assertEquals("p", request.url.queryParameter("project"))
        assertNull(request.url.queryParameter("session"))
    }

    @Test fun `microphone frames are sent as input_audio_buffer append, and never when muted`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("capture started") { io.onFrame != null }
        io.onFrame!!(ByteArray(960))
        await("frame received") { received.any { it.contains("input_audio_buffer.append") } }
        s.mute(true)
        val before = received.size
        io.onFrame!!(ByteArray(960))
        Thread.sleep(200)
        assertEquals(before, received.size)
        assertTrue(s.state.value.muted)
    }

    @Test fun `the person speaking over the agent drops what was queued for the speaker`() {
        upgrade {
            it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""")
            it.send("""{"type":"response.output_audio.delta","delta":"$audio","seq":2}""")
            it.send("""{"type":"input_audio_buffer.speech_started","seq":3}""")
        }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("barge-in") { io.flushes.get() >= 1 }
        await("back to listening") { s.state.value.phase == Phase.Listening }
    }

    @Test fun `the transcript is assembled from what was heard and what is said`() {
        upgrade {
            it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""")
            it.send("""{"type":"conversation.item.input_audio_transcription.completed","transcript":"Hello","seq":2}""")
            it.send("""{"type":"response.output_audio_transcript.delta","delta":"Hi ","seq":3}""")
            it.send("""{"type":"response.output_audio_transcript.delta","delta":"there","seq":4}""")
            it.send("""{"type":"response.output_audio_transcript.done","transcript":"Hi there","seq":5}""")
        }
        val s = session(FakeIo()).also { it.start() }
        await("transcript") { s.state.value.transcript.size == 2 && s.state.value.transcript[1].final }
        val lines = s.state.value.transcript
        assertEquals(Role.Person to "Hello", lines[0].role to lines[0].text)
        assertEquals(Role.Agent to "Hi there", lines[1].role to lines[1].text)
    }

    @Test fun `a spent budget ends the session with a plain notice and stops the audio`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}"""); it.close(1008, "session budget spent") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("ended") { s.state.value.phase == Phase.Ended }
        assertEquals(EndKind.SpendCap, s.state.value.notice?.kind)
        assertTrue(io.stopped.get() >= 1)
    }

    @Test fun `the agent's time cap ends it as a time limit`() {
        upgrade { it.close(1000, "max_session_minutes") }
        val s = session(FakeIo()).also { it.start() }
        await("ended") { s.state.value.phase == Phase.Ended }
        assertEquals(EndKind.TimeCap, s.state.value.notice?.kind)
    }

    @Test fun `a drop without a close frame resumes the same session after the last event read`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-9","seq":1}"""); it.send("""{"type":"response.created","seq":2}""") }
        upgrade { it.send("""{"type":"response.output_audio.delta","delta":"$audio","seq":3}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("first socket") { serverSockets.size == 1 && s.state.value.phase == Phase.Listening }
        Thread.sleep(100) // let both events be read
        rawSockets.first().close() // the link drops, no close frame
        await("resumed and spoke") { io.played.isNotEmpty() }
        server.takeRequest()
        val second = server.takeRequest()
        assertEquals("s-9", second.url.queryParameter("session"))
        assertEquals("2", second.url.queryParameter("after"))
        assertFalse(s.state.value.phase == Phase.Ended)
    }

    @Test fun `a drop that outlasts the resume window ends the session as a lost connection`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-9","seq":1}""") }
        server.enqueue(MockResponse.Builder().code(503).build())
        val s = session(FakeIo(), resumeWindowMs = 0).also { it.start() }
        await("first socket") { serverSockets.size == 1 }
        Thread.sleep(50)
        rawSockets.first().close()
        await("ended") { s.state.value.phase == Phase.Ended }
        assertEquals(EndKind.Dropped, s.state.value.notice?.kind)
    }

    @Test fun `a refused handshake says so and does not retry`() {
        server.enqueue(MockResponse.Builder().code(403).build())
        val s = session(FakeIo()).also { it.start() }
        await("ended") { s.state.value.phase == Phase.Ended }
        assertEquals(EndKind.Refused, s.state.value.notice?.kind)
        assertEquals(1, server.requestCount)
    }

    @Test fun `an expired sign-in asks to sign in again`() {
        server.enqueue(MockResponse.Builder().code(401).build())
        val s = session(FakeIo()).also { it.start() }
        await("ended") { s.state.value.phase == Phase.Ended }
        assertTrue(s.state.value.notice!!.title.contains("Sign in again"))
    }

    @Test fun `hanging up ends it and releases the audio`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("open") { s.state.value.phase == Phase.Listening }
        s.stop()
        assertEquals(Phase.Ended, s.state.value.phase)
        assertEquals(EndKind.Normal, s.state.value.notice?.kind)
        assertTrue(io.stopped.get() >= 1)
    }

    @Test fun `interrupting cancels the model's response and drops the queue`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("open") { s.state.value.phase == Phase.Listening }
        s.interrupt()
        await("cancel sent") { received.any { it.contains("response.cancel") } }
        assertTrue(io.flushes.get() >= 1)
    }

    @Test fun `nothing the phone sends is a session update`() {
        upgrade { it.send("""{"type":"choregos.session","session_id":"s-1","seq":1}""") }
        val io = FakeIo()
        val s = session(io).also { it.start() }
        await("capture started") { io.onFrame != null }
        io.onFrame!!(ByteArray(960)); s.sendText("hi"); s.interrupt()
        await("sent") { received.size >= 4 }
        assertTrue(received.none { it.contains("session.update") })
    }
}
