package systems.graal.tranibor.net

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun up() {
        server = MockWebServer().also { it.start() }
        api = ApiClient(server.url("/").toString().trimEnd('/'), { "chs_secret" })
    }

    @After fun down() = server.close()

    private fun json(code: Int, body: String) = MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

    @Test fun `a request carries the bearer token and goes under the api prefix`() = runBlocking {
        server.enqueue(json(200, """[{"slug":"acme","name":"Acme"}]"""))
        assertEquals("acme", api.orgs().single().slug)
        val request = server.takeRequest()
        assertEquals("/api/v1/orgs", request.url.encodedPath)
        assertEquals("Bearer chs_secret", request.headers["Authorization"])
    }

    @Test fun `sign-in sends no token`() = runBlocking {
        val anonymous = ApiClient(server.url("/").toString().trimEnd('/'), { null })
        server.enqueue(json(200, """{"device_code":"d","user_code":"U","verification_uri":"v","verification_uri_complete":"vc","expires_in":600,"interval":5}"""))
        anonymous.startDevice("Phone")
        val request = server.takeRequest()
        assertNull(request.headers["Authorization"])
        assertTrue(request.body!!.utf8().contains("\"client_kind\":\"cli\""))
    }

    @Test fun `a pending poll is a result and not an error`() = runBlocking {
        server.enqueue(json(400, """{"error":"authorization_pending"}"""))
        val polled = api.pollDevice("d") as ApiClient.PollResult.Pending
        assertEquals("authorization_pending", polled.error)
    }

    @Test fun `slow_down carries its interval`() = runBlocking {
        server.enqueue(json(400, """{"error":"slow_down","interval":10}"""))
        assertEquals(10, (api.pollDevice("d") as ApiClient.PollResult.Pending).interval)
    }

    @Test fun `a granted poll is the session`() = runBlocking {
        server.enqueue(json(200, """{"access_token":"chs_x","session_id":"s1","kind":"cli","expires_in":3600,"auth_time":"2026-11-19T10:00:00Z"}"""))
        val granted = api.pollDevice("d") as ApiClient.PollResult.Granted
        assertEquals("chs_x", granted.token.accessToken)
        assertEquals("s1", granted.token.sessionId)
    }

    @Test fun `a step-up refusal is told apart from a dead session`() {
        server.enqueue(json(401, """{"title":"Unauthorized","detail":"fresh sign-in","code":"step_up_required"}"""))
        server.enqueue(json(401, """{"title":"Unauthorized","detail":"gone"}"""))
        val stepUp = assertThrows(ApiException::class.java) { runBlocking { api.orgs() } }
        val dead = assertThrows(ApiException::class.java) { runBlocking { api.orgs() } }
        assertTrue(stepUp.needsFreshSignIn)
        assertFalse(stepUp.sessionEnded)
        assertTrue(dead.sessionEnded)
    }

    @Test fun `codes of a validation error are gathered`() {
        server.enqueue(json(422, """{"title":"Invalid","errors":[{"error":"decision_requires_session"}]}"""))
        val e = assertThrows(ApiException::class.java) { runBlocking { api.orgs() } }
        assertTrue(e.needsOwnSession)
    }

    @Test fun `an unreachable instance is a network error`() {
        server.close()
        assertThrows(NetworkException::class.java) { runBlocking { api.orgs() } }
    }

    @Test fun `a decision strips the api prefix the server echoes`() = runBlocking {
        server.enqueue(json(200, "{}"))
        api.decide("/api/v1/me/conversations/c1/actions/a1/confirm", null)
        assertEquals("/api/v1/me/conversations/c1/actions/a1/confirm", server.takeRequest().url.encodedPath)
    }

    @Test fun `a response stream delivers its events in order`() = runBlocking {
        server.enqueue(
            MockResponse.Builder().addHeader("Content-Type", "text/event-stream")
                .body("event: response.created\ndata: {}\n\nevent: response.output_text.delta\ndata: {\"delta\":\"Hi\"}\n\n").build(),
        )
        val seen = mutableListOf<String>()
        api.streamResponse("acme", "bot", AgentResponseCreate("p", "hello")) { seen += it.event }
        assertEquals(listOf("response.created", "response.output_text.delta"), seen)
        val request = server.takeRequest()
        assertEquals("/api/v1/orgs/acme/agents/bot/responses", request.url.encodedPath)
        assertTrue(request.body!!.utf8().contains("\"project\":\"p\""))
    }

    @Test fun `registering a device posts the keys the push service gave`() = runBlocking {
        server.enqueue(json(201, """{"id":"p1","device_name":"Pixel","endpoint_host":"ntfy.sh","kinds":[],"created_at":"2026-11-19T10:00:00Z"}"""))
        val made = api.registerPush(PushSubscriptionCreate("https://ntfy.sh/x", PushKeys("P", "A"), "Pixel"))
        assertEquals("p1", made.id)
        val body = server.takeRequest().body!!.utf8()
        assertTrue(body.contains("\"p256dh\":\"P\""))
        assertTrue(body.contains("\"device_name\":\"Pixel\""))
    }

    @Test fun `stopping a run, a session and a turn each use their own route`() = runBlocking {
        server.enqueue(json(200, "{}")); server.enqueue(json(204, "")); server.enqueue(json(200, CONVERSATION))
        fun entry(kind: String) = ActivityEntry(kind = kind, id = "x1", status = "running")
        api.stopActivity("acme", entry("run"), "why")
        api.stopActivity("acme", entry("session"), "why")
        api.stopActivity("acme", entry("turn"), "why")
        assertEquals("POST /api/v1/runs/x1/cancel", server.takeRequest().let { "${it.method} ${it.url.encodedPath}" })
        assertEquals("DELETE /api/v1/orgs/acme/agent-sessions/x1", server.takeRequest().let { "${it.method} ${it.url.encodedPath}" })
        assertEquals("POST /api/v1/me/conversations/x1/cancel", server.takeRequest().let { "${it.method} ${it.url.encodedPath}" })
    }

    private companion object {
        const val CONVERSATION = """{"id":"x1","agent":"bot","status":"running"}"""
    }
}
