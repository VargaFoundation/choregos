package systems.graal.tranibor.activity

import systems.graal.tranibor.net.ActivityEntry
import systems.graal.tranibor.net.ActivityList
import systems.graal.tranibor.net.ApiJson
import systems.graal.tranibor.net.SseEvent

/** The activity of an organisation as the screen holds it, and how a stream event changes it. */
data class ActivityFeed(val inFlight: List<ActivityEntry>, val finished: List<ActivityEntry>) {
    companion object {
        const val FINISHED_MAX = 50

        fun of(list: ActivityList) = ActivityFeed(list.inFlight, list.finished)

        /** The feed after [event], or `null` when the event is not one of the feed's (a heartbeat, a name the server adds later). */
        fun apply(feed: ActivityFeed, event: SseEvent): ActivityFeed? {
            if (event.event != "activity.upsert" && event.event != "activity.ended") return null
            val entry = try {
                ApiJson.decodeFromString(ActivityEntry.serializer(), event.data)
            } catch (_: Exception) {
                return null
            }
            val others = feed.inFlight.filterNot { it.id == entry.id }
            return if (event.event == "activity.ended") {
                ActivityFeed(others, (listOf(entry) + feed.finished.filterNot { it.id == entry.id }).take(FINISHED_MAX))
            } else {
                ActivityFeed(listOf(entry) + others, feed.finished.filterNot { it.id == entry.id })
            }
        }

        fun title(entry: ActivityEntry): String =
            entry.workItemTitle?.takeIf { it.isNotBlank() }
                ?: entry.agentSlug?.takeIf { it.isNotBlank() }
                ?: entry.workflow?.takeIf { it.isNotBlank() }
                ?: entry.assistant?.takeIf { it.isNotBlank() }
                ?: entry.id
    }
}
