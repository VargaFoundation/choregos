package systems.graal.tranibor.activity

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import systems.graal.tranibor.net.ActivityEntry
import systems.graal.tranibor.net.ActivityList
import systems.graal.tranibor.net.SseEvent

class ActivityFeedTest {
    private fun entry(id: String, status: String = "running") = ActivityEntry(kind = "run", id = id, status = status)
    private fun event(name: String, id: String, status: String = "running") =
        SseEvent(name, """{"kind":"run","id":"$id","status":"$status","started_at":"2026-11-19T10:00:00Z","can_stop":true}""", null)

    @Test fun `an upsert adds a new entry in front and replaces a known one`() {
        var feed = ActivityFeed.of(ActivityList(inFlight = listOf(entry("a"))))
        feed = ActivityFeed.apply(feed, event("activity.upsert", "b"))!!
        assertEquals(listOf("b", "a"), feed.inFlight.map { it.id })
        feed = ActivityFeed.apply(feed, event("activity.upsert", "a", "queued"))!!
        assertEquals(listOf("a", "b"), feed.inFlight.map { it.id })
        assertEquals("queued", feed.inFlight.first().status)
    }

    @Test fun `an entry that ended leaves the running list and heads the finished one`() {
        val feed = ActivityFeed.apply(ActivityFeed.of(ActivityList(inFlight = listOf(entry("a")))), event("activity.ended", "a", "succeeded"))!!
        assertEquals(emptyList<String>(), feed.inFlight.map { it.id })
        assertEquals(listOf("a"), feed.finished.map { it.id })
    }

    @Test fun `the finished list is bounded`() {
        var feed = ActivityFeed(emptyList(), emptyList())
        repeat(60) { feed = ActivityFeed.apply(feed, event("activity.ended", "e$it", "succeeded"))!! }
        assertEquals(ActivityFeed.FINISHED_MAX, feed.finished.size)
        assertEquals("e59", feed.finished.first().id)
    }

    @Test fun `a heartbeat or an event of a later version is not the feed's`() {
        val feed = ActivityFeed(emptyList(), emptyList())
        assertNull(ActivityFeed.apply(feed, SseEvent("ping", "", null)))
        assertNull(ActivityFeed.apply(feed, SseEvent("activity.upsert", "garbage", null)))
    }

    @Test fun `the title is the most telling name the entry has`() {
        assertEquals("Fix bug", ActivityFeed.title(entry("a").copy(workItemTitle = "Fix bug", agentSlug = "bot")))
        assertEquals("bot", ActivityFeed.title(entry("a").copy(agentSlug = "bot")))
        assertEquals("a", ActivityFeed.title(entry("a")))
    }
}
