package systems.graal.tranibor.voice

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import systems.graal.tranibor.net.ApiJson

/** What the relay says to a caller, reduced to what a phone reacts to. [seq] numbers every event of a session. */
sealed interface Inbound {
    val seq: Int?

    /** The first event of a session: its id, the key to come back with after a drop. */
    data class Session(val id: String, override val seq: Int?) : Inbound
    data class AudioDelta(val pcm: ByteArray, override val seq: Int?) : Inbound
    data class AudioDone(override val seq: Int?) : Inbound
    data class SpeechStarted(override val seq: Int?) : Inbound
    data class SpeechStopped(override val seq: Int?) : Inbound

    /** What the person said, as the model heard it. */
    data class UserTranscript(val text: String, override val seq: Int?) : Inbound
    data class AssistantTranscriptDelta(val text: String, override val seq: Int?) : Inbound
    data class AssistantTranscriptDone(val text: String, override val seq: Int?) : Inbound
    data class ResponseCreated(override val seq: Int?) : Inbound
    data class ResponseDone(override val seq: Int?) : Inbound
    data class Error(val code: String?, val message: String, override val seq: Int?) : Inbound

    /** An event the phone has no use for (`session.created`, `rate_limits.updated`, ...). */
    data class Ignored(val type: String, override val seq: Int?) : Inbound
}

/**
 * The OpenAI Realtime events of the relay (`GET /orgs/{org}/agents/{slug}/realtime`), both in their GA names
 * and in the beta ones the gateway may still use. The phone never sends `session.update`: the agent owns the
 * session's configuration, and the relay refuses it.
 */
object RealtimeCodec {
    fun parse(text: String): Inbound? {
        val obj = try {
            ApiJson.parseToJsonElement(text).jsonObject
        } catch (_: Exception) {
            return null
        }
        val type = obj.str("type") ?: return null
        val seq = (obj["seq"] as? JsonPrimitive)?.intOrNull
        return when (type) {
            "choregos.session" -> obj.str("session_id")?.let { Inbound.Session(it, seq) }
            "response.output_audio.delta", "response.audio.delta" ->
                obj.str("delta")?.let(Pcm::decode)?.let { Inbound.AudioDelta(it, seq) }
            "response.output_audio.done", "response.audio.done" -> Inbound.AudioDone(seq)
            "input_audio_buffer.speech_started" -> Inbound.SpeechStarted(seq)
            "input_audio_buffer.speech_stopped" -> Inbound.SpeechStopped(seq)
            "conversation.item.input_audio_transcription.completed" ->
                obj.str("transcript")?.let { Inbound.UserTranscript(it.trim(), seq) }
            "response.output_audio_transcript.delta", "response.audio_transcript.delta" ->
                obj.str("delta")?.let { Inbound.AssistantTranscriptDelta(it, seq) }
            "response.output_audio_transcript.done", "response.audio_transcript.done" ->
                obj.str("transcript")?.let { Inbound.AssistantTranscriptDone(it.trim(), seq) }
            "response.created" -> Inbound.ResponseCreated(seq)
            "response.done" -> Inbound.ResponseDone(seq)
            "error" -> {
                val error = obj["error"] as? JsonObject
                Inbound.Error(error?.str("code"), error?.str("message") ?: "The voice relay reported an error.", seq)
            }
            else -> Inbound.Ignored(type, seq)
        }
    }

    /** One slice of the microphone. */
    fun append(pcm: ByteArray): String = buildJsonObject {
        put("type", "input_audio_buffer.append")
        put("audio", Pcm.encode(pcm))
    }.toString()

    /** The person cut the agent off by hand: the model stops talking. (The server's own VAD does it when they speak.) */
    fun cancel(): String = buildJsonObject { put("type", "response.cancel") }.toString()

    /** A typed line instead of speech: a message of the person, then the model's turn. */
    fun userText(text: String): List<String> = listOf(
        buildJsonObject {
            put("type", "conversation.item.create")
            putJsonObject("item") {
                put("type", "message")
                put("role", "user")
                putJsonArray("content") { add(buildJsonObject { put("type", "input_text"); put("text", text) }) }
            }
        }.toString(),
        buildJsonObject { put("type", "response.create") }.toString(),
    )

    private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
}
