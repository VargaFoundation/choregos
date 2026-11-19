package systems.graal.tranibor.chat

import systems.graal.tranibor.net.AgentDelta
import systems.graal.tranibor.net.AgentPendingAction
import systems.graal.tranibor.net.AgentResponse
import systems.graal.tranibor.net.ApiJson
import systems.graal.tranibor.net.SseEvent

/** What a text stream (`POST …/responses`, or a message of a conversation) says, reduced to what the screen reacts to. */
sealed interface ChatEvent {
    data class Delta(val text: String) : ChatEvent
    data class Started(val sessionId: String) : ChatEvent

    /** The turn is over; [pending] are the actions that wait for the person's decision (the inbox shows them). */
    data class Done(val sessionId: String, val pending: List<AgentPendingAction>) : ChatEvent
    data class Failed(val message: String) : ChatEvent
}

object ChatEvents {
    /** `null` for an event the screen does not care about (an unknown name is never an error: the server may add some). */
    fun parse(event: SseEvent): ChatEvent? = try {
        when (event.event) {
            "response.output_text.delta" -> ChatEvent.Delta(ApiJson.decodeFromString(AgentDelta.serializer(), event.data).delta)
            "response.created" -> ChatEvent.Started(response(event).sessionId)
            "response.completed" -> response(event).let { ChatEvent.Done(it.sessionId, it.pendingActions) }
            "response.failed" -> response(event).let {
                ChatEvent.Failed(it.error?.message?.takeIf { m -> m.isNotBlank() } ?: "The agent could not answer.")
            }
            "error" -> ChatEvent.Failed(refusal(event.data))
            else -> null
        }
    } catch (_: Exception) {
        null
    }

    /** An `error` event: a refusal before the turn ran (`detail` or `text`, as the CLI reads it). */
    private fun refusal(data: String): String {
        val root = runCatching { ApiJson.parseToJsonElement(data) as? kotlinx.serialization.json.JsonObject }.getOrNull()
        fun field(key: String) = (root?.get(key) as? kotlinx.serialization.json.JsonPrimitive)?.content?.takeIf { it.isNotBlank() }
        return field("detail") ?: field("text") ?: "The turn was refused."
    }

    private fun response(event: SseEvent): AgentResponse {
        val text = event.data
        // The server wraps the response in `{"response": {...}}` on some events and sends it bare on others.
        val root = ApiJson.parseToJsonElement(text)
        val inner = (root as? kotlinx.serialization.json.JsonObject)?.get("response") ?: root
        return ApiJson.decodeFromJsonElement(AgentResponse.serializer(), inner)
    }
}
