package systems.graal.tranibor.push

import android.net.Uri
import systems.graal.tranibor.net.ApiJson
import systems.graal.tranibor.net.PushPayload
import systems.graal.tranibor.pending.Decisions

/** Where in the app a notification, or a link, leads. */
sealed interface Destination {
    /** What waits for the person; [id] is the item to show first, when there is one. */
    data class Inbox(val id: String? = null) : Destination
    data class Activity(val org: String, val id: String? = null) : Destination
    data class Agent(val org: String, val slug: String) : Destination
    data object Account : Destination
}

/** The text a notification shows: the payload's own, cleaned (it came from the instance, but a title is still shown on a lock screen). */
data class Shown(val title: String, val text: String, val link: Uri)

object PushMessages {
    const val SCHEME = "tranibor"

    /** The payload of a decrypted push, or `null` when it is not one of ours. */
    fun parse(content: ByteArray): PushPayload? = try {
        ApiJson.decodeFromString(PushPayload.serializer(), content.decodeToString())
    } catch (_: Exception) {
        null
    }

    /** What to show, and the deep link the tap opens. A kind the app does not know is still shown (it opens the inbox). */
    fun shown(payload: PushPayload): Shown {
        val title = Decisions.clean(payload.title).take(120).ifBlank { "Tranibor" }
        val text = Decisions.clean(payload.body).take(200)
        return Shown(title, text, linkFor(payload))
    }

    /** `tranibor://decision/<id>`, `tranibor://run/<org>/<id>`, `tranibor://agent/<org>/<slug>`, `tranibor://session/<org>/<id>`. */
    fun linkFor(payload: PushPayload): Uri {
        val ref = payload.ref
        val builder = Uri.Builder().scheme(SCHEME)
        return when {
            payload.kind == "decision.awaiting" && ref.id.isNotEmpty() -> builder.authority("decision").appendPath(ref.id)
            payload.kind == "run.ended" && ref.id.isNotEmpty() ->
                builder.authority("run").appendPath(payload.org).appendPath(ref.id)
            payload.kind == "session.ended" && ref.id.isNotEmpty() ->
                builder.authority("session").appendPath(payload.org).appendPath(ref.id)
            payload.kind == "agent.notification" && ref.type == "agent" && ref.id.isNotEmpty() ->
                builder.authority("agent").appendPath(payload.org).appendPath(ref.id)
            else -> builder.authority("inbox")
        }.build()
    }

    /** Resolves a link (from a notification, or any `tranibor://` link) into a place, refusing what it does not understand. */
    fun destination(link: Uri): Destination? {
        if (link.scheme != SCHEME) return null
        val parts = link.pathSegments
        return when (link.authority) {
            "inbox" -> Destination.Inbox()
            "decision" -> parts.firstOrNull()?.let { Destination.Inbox(it) } ?: Destination.Inbox()
            "run", "session" -> if (parts.size == 2) Destination.Activity(parts[0], parts[1]) else null
            "agent" -> if (parts.size == 2) Destination.Agent(parts[0], parts[1]) else null
            "account" -> Destination.Account
            else -> null
        }
    }
}
