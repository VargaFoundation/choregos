package systems.graal.tranibor.net

import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Test

class SseTest {
    private fun read(text: String): List<SseEvent> {
        val out = mutableListOf<SseEvent>()
        readSse(Buffer().writeUtf8(text), out::add)
        return out
    }

    @Test fun `an event has a name and its data`() {
        assertEquals(listOf(SseEvent("response.created", "{\"a\":1}", null)), read("event: response.created\ndata: {\"a\":1}\n\n"))
    }

    @Test fun `the name defaults to message`() = assertEquals("message", read("data: x\n\n").single().event)

    @Test fun `data may span lines`() = assertEquals("a\nb", read("data: a\ndata: b\n\n").single().data)

    @Test fun `comments are dropped`() = assertEquals(1, read(": ping\n\ndata: x\n\n").size)

    @Test fun `CRLF ends a line as LF does`() {
        assertEquals(listOf("a", "b"), read("data: a\r\n\r\ndata: b\r\n\r\nrest").map { it.data })
    }

    @Test fun `an event cut off by the end of the stream is not delivered`() = assertEquals(emptyList<SseEvent>(), read("data: half"))

    @Test fun `the id is kept`() = assertEquals("7", read("id: 7\ndata: x\n\n").single().id)
}
