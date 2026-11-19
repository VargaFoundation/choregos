package systems.graal.tranibor.auth

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.graal.tranibor.net.ApiClient
import systems.graal.tranibor.net.DeviceAuthorization

class AuthTest {
    private class MemoryBlob : BlobStore {
        var blob: ByteArray? = null
        override fun read() = blob
        override fun write(blob: ByteArray?) { this.blob = blob }
    }

    @Test fun `a sealed secret opens to what was sealed and is not readable as it is`() {
        val box = AesGcmBox.inMemory()
        val sealed = box.seal("chs_token".encodeToByteArray())
        assertFalse(sealed.decodeToString().contains("chs_token"))
        assertArrayEquals("chs_token".encodeToByteArray(), box.open(sealed))
    }

    @Test fun `two seals of the same secret differ`() {
        val box = AesGcmBox.inMemory()
        assertFalse(box.seal(byteArrayOf(1, 2, 3)).contentEquals(box.seal(byteArrayOf(1, 2, 3))))
    }

    @Test fun `a blob tampered with, or sealed by another key, does not open`() {
        val sealed = AesGcmBox.inMemory().seal(byteArrayOf(1, 2, 3))
        assertNull(AesGcmBox.inMemory(ByteArray(32) { 9 }).open(sealed))
        sealed[sealed.size - 1] = (sealed.last() + 1).toByte()
        assertNull(AesGcmBox.inMemory().open(sealed))
    }

    @Test fun `a session survives a restart and is stored sealed`() {
        val blobs = MemoryBlob()
        val box = AesGcmBox.inMemory()
        SessionStore(box, blobs).save(Session("https://t.example.com", "chs_abc", "s1", "2026-11-19T10:00:00Z"))
        assertFalse(blobs.blob!!.decodeToString().contains("chs_abc"))
        assertEquals("chs_abc", SessionStore(box, blobs).load()?.token)
    }

    @Test fun `a blob that does not open is cleared, not kept`() {
        val blobs = MemoryBlob().also { it.blob = byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18) }
        assertNull(SessionStore(AesGcmBox.inMemory(), blobs).load())
        assertNull(blobs.blob)
    }

    @Test fun `clearing forgets the session`() {
        val blobs = MemoryBlob()
        val store = SessionStore(AesGcmBox.inMemory(), blobs)
        store.save(Session("https://t", "x", "s"))
        store.clear()
        assertNull(store.load())
    }

    // ───────────── the device flow ─────────────

    private fun codes(expires: Int = 600, interval: Int = 5) =
        DeviceAuthorization("dev", "ABCD-EFGH", "https://t/device", "https://t/device?user_code=ABCD-EFGH", expires, interval)

    private fun flow(server: MockWebServer, waits: MutableList<Long>, clock: () -> Long = { 0L }) =
        DeviceSignIn(ApiClient(server.url("/").toString().trimEnd('/'), { null }), "Phone", now = clock, wait = { waits += it })

    private fun reply(code: Int, body: String) = MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

    @Test fun `the link can ask for a fresh sign-in`() = runBlocking {
        val server = MockWebServer().also { it.start() }
        val flow = flow(server, mutableListOf())
        assertEquals("https://t/device?user_code=ABCD-EFGH", flow.link(codes(), fresh = false))
        assertEquals("https://t/device?user_code=ABCD-EFGH&fresh=1", flow.link(codes(), fresh = true))
        server.close()
    }

    @Test fun `it polls at the interval until the person approves`() = runBlocking {
        val server = MockWebServer().also { it.start() }
        server.enqueue(reply(400, """{"error":"authorization_pending"}"""))
        server.enqueue(reply(200, """{"access_token":"chs_x","session_id":"s1","kind":"cli","expires_in":3600}"""))
        val waits = mutableListOf<Long>()
        val token = flow(server, waits).await(codes())
        assertEquals("chs_x", token.accessToken)
        assertEquals(listOf(5000L, 5000L), waits)
        server.close()
    }

    @Test fun `slow_down lengthens the wait`() = runBlocking {
        val server = MockWebServer().also { it.start() }
        server.enqueue(reply(400, """{"error":"slow_down","interval":10}"""))
        server.enqueue(reply(200, """{"access_token":"chs_x","session_id":"s1","kind":"cli","expires_in":3600}"""))
        val waits = mutableListOf<Long>()
        flow(server, waits).await(codes())
        assertEquals(listOf(5000L, 10000L), waits)
        server.close()
    }

    @Test fun `a refusal in the console ends the sign-in with words`() {
        val server = MockWebServer().also { it.start() }
        server.enqueue(reply(400, """{"error":"access_denied"}"""))
        val e = assertThrows(SignInFailed::class.java) { runBlocking { flow(server, mutableListOf()).await(codes()) } }
        assertTrue(e.message!!.contains("refused"))
        server.close()
    }

    @Test fun `an expired code ends the sign-in`() {
        val server = MockWebServer().also { it.start() }
        repeat(3) { server.enqueue(reply(400, """{"error":"authorization_pending"}""")) }
        var t = 0L
        val e = assertThrows(SignInFailed::class.java) {
            runBlocking { flow(server, mutableListOf(), clock = { t.also { t += 400_000 } }).await(codes(expires = 600)) }
        }
        assertNotEquals("", e.message)
        server.close()
    }
}
