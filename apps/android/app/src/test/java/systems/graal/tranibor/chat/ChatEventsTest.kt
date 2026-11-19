package systems.graal.tranibor.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.graal.tranibor.net.SseEvent

class ChatEventsTest {
    private fun parse(event: String, data: String) = ChatEvents.parse(SseEvent(event, data, null))

    @Test fun `a delta carries text`() = assertEquals(ChatEvent.Delta("Hel"), parse("response.output_text.delta", """{"response_id":"r","delta":"Hel"}"""))

    @Test fun `a response is read bare or wrapped`() {
        assertEquals(ChatEvent.Started("s1"), parse("response.created", """{"id":"r","session_id":"s1"}"""))
        assertEquals(ChatEvent.Started("s1"), parse("response.created", """{"response":{"id":"r","session_id":"s1"}}"""))
    }

    @Test fun `a completed turn lists what waits for a decision`() {
        val done = parse(
            "response.completed",
            """{"id":"r","session_id":"s1","status":"completed","pending_actions":[{"id":"a","summary":"write","confirm_url":"u"}]}""",
        ) as ChatEvent.Done
        assertEquals(1, done.pending.size)
    }

    @Test fun `a failed turn says why`() {
        val failed = parse("response.failed", """{"status":"failed","error":{"code":"x","message":"budget spent"}}""") as ChatEvent.Failed
        assertEquals("budget spent", failed.message)
    }

    @Test fun `a refusal before the turn ran is read like the CLI reads it`() {
        assertEquals(ChatEvent.Failed("no seat"), parse("error", """{"status":409,"detail":"no seat"}"""))
    }

    @Test fun `an unknown event or broken data is not an error`() {
        assertNull(parse("response.something_new", "{}"))
        assertNull(parse("response.output_text.delta", "not json"))
    }
}
