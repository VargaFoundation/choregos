package systems.graal.tranibor.pending

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import systems.graal.tranibor.net.PendingItem

/**
 * What waits for the person and how it is decided: a port of `packages/cli/src/tranibor_cli/decide.py`, so a
 * phone and a terminal read the same record and send the same request (ADR 0154 section 7). The phone adds
 * no route: a decision is the route the item itself carries (`decision.path`).
 */
object Decisions {
    enum class Verb(val wire: String) { Approve("approve"), Reject("reject"), Answer("answer") }

    fun kindLabel(kind: String): String = when (kind) {
        "conversation_action" -> "Write proposed by an assistant"
        "launch" -> "Workflow start proposed by an assistant"
        "governed_action" -> "Governed action awaiting approval"
        "human_step" -> "Human step"
        else -> kind
    }

    /** Where the item's own record is read: its operation, arguments and diff are not in the list. */
    fun detailPath(item: PendingItem): String = when (item.kind) {
        "conversation_action", "launch" -> "/me/conversations/${item.where?.conversationId}/actions"
        "governed_action" -> item.decision.path.removeSuffix("/decision")
        else -> "/work-items/${item.where?.workItemId}"
    }

    /** The record out of what [detailPath] answered; `null` when it is not waiting any more. */
    fun pickDetail(item: PendingItem, payload: JsonElement): JsonObject? {
        val obj = payload as? JsonObject ?: return null
        return when (item.kind) {
            "conversation_action", "launch" ->
                (obj["items"] as? JsonArray)?.firstOrNull { (it as? JsonObject)?.str("id") == item.id } as? JsonObject
            "governed_action" -> obj.takeIf { it.str("id") == item.id }
            else -> (obj["pending_request"] as? JsonObject)?.takeIf { it.str("id") == item.id }
        }
    }

    /** What the person may do here. A task's form and a scope change's paths stay in the console. */
    fun verbs(item: PendingItem, detail: JsonObject?): List<Verb> {
        if (detail == null) return emptyList()
        if (item.kind != "human_step") return listOf(Verb.Approve, Verb.Reject)
        return when (detail.str("kind")) {
            "approval" -> listOf(Verb.Approve, Verb.Reject)
            "question" -> listOf(Verb.Answer)
            else -> emptyList()
        }
    }

    /** Every rejection asks a reason, an assistant's proposed write or launch included. */
    fun asksReason(verb: Verb): Boolean = verb == Verb.Reject

    /** The route and body of ONE decision on ONE item. */
    fun requestFor(item: PendingItem, verb: Verb, reason: String = "", answer: String = ""): Pair<String, JsonObject?> {
        val path = item.decision.path
        when (item.kind) {
            "conversation_action", "launch" ->
                return if (verb == Verb.Approve) path to null
                else path.removeSuffix("/confirm") + "/refuse" to buildJsonObject { put("reason", reason) }
            "governed_action" ->
                return path to buildJsonObject {
                    put("decision", verb.wire)
                    if (reason.isNotEmpty()) put("reason", reason) else put("reason", JsonNull)
                }
        }
        return path to buildJsonObject {
            put("request_id", item.id)
            put("kind", if (verb == Verb.Answer) "answer" else verb.wire)
            if (verb == Verb.Answer) put("answer", answer)
            if (reason.isNotEmpty()) put("reason", reason)
        }
    }

    /** The lines a person reads before deciding: what it is, where, and its arguments (a long value is cut, and says so). */
    fun describe(item: PendingItem, detail: JsonObject?): List<String> {
        val lines = mutableListOf(kindLabel(item.kind), clean(item.what))
        val w = item.where
        val place = listOfNotNull(w?.org, w?.projectSlug, w?.workItemKey, w?.conversationId).joinToString(" · ") { clean(it) }
        if (place.isNotEmpty()) lines += "in $place"
        if (detail == null) {
            lines += "Its record could not be read: it may not be waiting any more."
            return lines
        }
        when (item.kind) {
            "conversation_action" -> lines += "operation: ${clean(detail.str("need"))}.${clean(detail.str("operation"))}"
            "launch" -> {
                val target = detail["target"] as? JsonObject
                lines += "launch: workflow ${clean(target?.str("workflow"))} in project ${clean(target?.str("project"))}"
            }
            "governed_action" -> {
                lines += "operation: ${clean(detail.str("kind"))} (from ${clean(detail.str("origin"))})"
                detail.str("justification")?.let { lines += "why: ${shown(JsonPrimitive(it))}" }
            }
            else -> lines += "asks: ${clean(detail.str("kind"))}"
        }
        val arguments = when (item.kind) {
            "conversation_action", "launch" -> detail["arguments"]
            "governed_action" -> detail["params"]
            else -> detail["payload"]
        } as? JsonObject
        if (!arguments.isNullOrEmpty()) {
            lines += "arguments:"
            arguments.forEach { (k, v) -> lines += "  ${clean(k)}: ${shown(v)}" }
        }
        if (item.kind == "governed_action") {
            (detail["effects"] as? JsonArray)?.takeIf { it.isNotEmpty() }?.let { effects ->
                lines += "effects:"
                effects.forEach { lines += "  ${shown(it)}" }
            }
        }
        (detail["diff"] as? JsonObject)?.takeIf { it.isNotEmpty() }?.let { diff ->
            lines += "diff:"
            diff.forEach { (k, v) ->
                val text = (v as? JsonPrimitive)?.contentOrNull
                if (text != null && '\n' in text) {
                    lines += "  ${clean(k)}:"
                    text.split('\n').forEach { lines += "    ${clean(it)}" }
                } else {
                    lines += "  ${clean(k)}: ${shown(v)}"
                }
            }
        }
        return lines
    }

    const val MAX_VALUE = 1500

    private fun shown(value: JsonElement): String {
        val text = clean((value as? JsonPrimitive)?.takeIf { it.isString }?.content ?: value.toString())
        return if (text.length > MAX_VALUE) "${text.take(MAX_VALUE)}… (${text.length - MAX_VALUE} more characters)" else text
    }

    /** Text that came from a third party (an agent, a workflow): control characters never reach the screen. */
    fun clean(text: String?): String =
        (text ?: "").map { if ((it.isISOControl() || Character.getType(it) == Character.FORMAT.toInt()) && it != '\n' && it != '\t') ' ' else it }.joinToString("")

    private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
}
