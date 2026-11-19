package systems.graal.tranibor.voice

import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.graal.tranibor.net.ApiJson

class RealtimeCodecTest {
    @Test fun `the first event names the session`() =
        assertEquals(Inbound.Session("s-1", 1), RealtimeCodec.parse("""{"type":"choregos.session","session_id":"s-1","seq":1}"""))

    @Test fun `audio arrives under the GA name and the beta one`() {
        val b64 = Pcm.encode(byteArrayOf(1, 0, 2, 0))
        for (type in listOf("response.output_audio.delta", "response.audio.delta")) {
            val event = RealtimeCodec.parse("""{"type":"$type","delta":"$b64","seq":4}""") as Inbound.AudioDelta
            assertEquals(4, event.pcm.size)
            assertEquals(4, event.seq)
        }
    }

    @Test fun `what the person said and what the agent says`() {
        assertEquals(
            Inbound.UserTranscript("hello", 2),
            RealtimeCodec.parse("""{"type":"conversation.item.input_audio_transcription.completed","transcript":" hello ","seq":2}"""),
        )
        assertEquals(Inbound.AssistantTranscriptDelta("Hi", 3), RealtimeCodec.parse("""{"type":"response.output_audio_transcript.delta","delta":"Hi","seq":3}"""))
        assertEquals(Inbound.AssistantTranscriptDone("Hi there", 5), RealtimeCodec.parse("""{"type":"response.audio_transcript.done","transcript":"Hi there","seq":5}"""))
    }

    @Test fun `speech started is the barge-in signal`() =
        assertTrue(RealtimeCodec.parse("""{"type":"input_audio_buffer.speech_started"}""") is Inbound.SpeechStarted)

    @Test fun `an error keeps the relay's words`() {
        val e = RealtimeCodec.parse("""{"type":"error","error":{"code":"x","message":"nope"}}""") as Inbound.Error
        assertEquals("nope", e.message)
    }

    @Test fun `an event the phone has no use for is ignored, and a non-event is nothing`() {
        assertTrue(RealtimeCodec.parse("""{"type":"rate_limits.updated"}""") is Inbound.Ignored)
        assertNull(RealtimeCodec.parse("not json"))
        assertNull(RealtimeCodec.parse("""{"no_type":1}"""))
    }

    @Test fun `a microphone slice is an append event carrying base64`() {
        val sent = ApiJson.parseToJsonElement(RealtimeCodec.append(byteArrayOf(1, 2))).jsonObject
        assertEquals("input_audio_buffer.append", sent["type"]!!.jsonPrimitive.content)
        assertEquals(Pcm.encode(byteArrayOf(1, 2)), sent["audio"]!!.jsonPrimitive.content)
    }

    @Test fun `the phone never sends session update, and only sends what the relay allows`() {
        val allowed = setOf(
            "input_audio_buffer.append", "input_audio_buffer.commit", "input_audio_buffer.clear", "response.cancel",
            "conversation.item.truncate", "conversation.item.delete", "conversation.item.create", "response.create",
        )
        val everything = listOf(RealtimeCodec.append(byteArrayOf(0, 0)), RealtimeCodec.cancel()) + RealtimeCodec.userText("hi")
        for (message in everything) {
            val type = ApiJson.parseToJsonElement(message).jsonObject["type"]!!.jsonPrimitive.content
            assertTrue(type, type in allowed)
            assertFalse(type == "session.update")
        }
    }

    @Test fun `a typed line is a user message then a response request without a body`() {
        val (create, respond) = RealtimeCodec.userText("hello").map { ApiJson.parseToJsonElement(it).jsonObject }
        assertEquals("user", create["item"]!!.jsonObject["role"]!!.jsonPrimitive.content)
        assertEquals(setOf("type"), respond.keys)
    }
}
