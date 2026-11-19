package systems.graal.tranibor.push

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import systems.graal.tranibor.KeyValue
import systems.graal.tranibor.net.ApiClient

class PushRegistrarTest {
    private class FakePlatform(var installed: List<String> = listOf("io.ntfy")) : PushPlatform {
        var registered: Pair<String, String>? = null
        var unregistered = 0
        override fun distributors() = installed
        override fun register(distributor: String, vapid: String) { registered = distributor to vapid }
        override fun unregister() { unregistered++ }
    }

    private class MemoryKv : KeyValue {
        val map = mutableMapOf<String, String>()
        override fun get(key: String) = map[key]
        override fun put(key: String, value: String?) { if (value == null) map.remove(key) else map[key] = value }
    }

    private lateinit var server: MockWebServer
    private val scope = CoroutineScope(Dispatchers.Default)
    private val platform = FakePlatform()
    private val kv = MemoryKv()
    private lateinit var registrar: PushRegistrar

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        val api = ApiClient(server.url("/").toString().trimEnd('/'), { "chs_x" })
        registrar = PushRegistrar({ api }, scope, platform, kv, "Pixel 8")
    }

    @After fun down() { scope.cancel(); server.close() }

    private fun json(code: Int, body: String) = MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

    private fun await(what: String, cond: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!cond()) { check(System.currentTimeMillis() < until) { "timed out: $what" }; Thread.sleep(10) }
    }

    private val sub = """{"id":"p1","device_name":"Pixel 8","endpoint_host":"ntfy.sh","kinds":[],"created_at":"2026-11-19T10:00:00Z"}"""

    @Test fun `turning on asks the instance for its key and hands it to the distributor`() {
        server.enqueue(json(200, """{"public_key":"BKEY"}"""))
        registrar.enable("io.ntfy")
        await("registered") { platform.registered != null }
        assertEquals("io.ntfy" to "BKEY", platform.registered)
        assertEquals("/api/v1/push/vapid-public-key", server.takeRequest().url.encodedPath)
    }

    @Test fun `an instance without push says so in words`() {
        server.enqueue(json(404, """{"title":"Not found","code":"push_not_configured"}"""))
        registrar.enable("io.ntfy")
        await("message") { registrar.state.value.message != null }
        assertEquals("This instance has no push notifications set up.", registrar.state.value.message)
        assertNull(platform.registered)
    }

    @Test fun `an endpoint from the distributor is registered with its keys and remembered`() {
        server.enqueue(json(201, sub))
        registrar.endpointChanged("https://ntfy.sh/abc", "PUB", "AUTH")
        await("stored") { kv.get("subscription") == "p1" }
        val body = server.takeRequest().body!!.utf8()
        assertTrue(body.contains("\"endpoint\":\"https://ntfy.sh/abc\""))
        assertTrue(body.contains("\"p256dh\":\"PUB\"") && body.contains("\"auth\":\"AUTH\""))
        assertTrue(body.contains("\"device_name\":\"Pixel 8\""))
        assertEquals("p1", registrar.state.value.subscriptionId)
    }

    @Test fun `turning off tells the distributor and the instance`() {
        kv.put("subscription", "p1")
        server.enqueue(MockResponse.Builder().code(204).build())
        registrar.disable()
        await("deleted") { server.requestCount == 1 }
        assertEquals("DELETE /api/v1/me/push-subscriptions/p1", server.takeRequest().let { "${it.method} ${it.url.encodedPath}" })
        assertEquals(1, platform.unregistered)
        assertNull(kv.get("subscription"))
    }

    @Test fun `when the distributor drops the registration the instance is told, the distributor is not called back`() {
        kv.put("subscription", "p1")
        server.enqueue(MockResponse.Builder().code(204).build())
        registrar.unregisteredByDistributor()
        await("deleted") { server.requestCount == 1 }
        assertEquals(0, platform.unregistered)
    }

    @Test fun `a test the push service says is gone clears the registration`() {
        kv.put("subscription", "p1")
        registrar.refresh()
        server.enqueue(json(200, """{"delivered":false,"status_code":410,"removed":true}"""))
        registrar.sendTest()
        await("cleared") { registrar.state.value.subscriptionId == null && registrar.state.value.message != null }
        assertNull(kv.get("subscription"))
    }

    @Test fun `a delivered test says it will arrive`() {
        kv.put("subscription", "p1")
        server.enqueue(json(200, """{"delivered":true,"status_code":201,"removed":false}"""))
        registrar.sendTest()
        await("message") { registrar.state.value.message?.startsWith("Sent") == true }
    }
}
