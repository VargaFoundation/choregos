package systems.graal.tranibor.push

import android.net.Uri
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import systems.graal.tranibor.net.PushPayload
import systems.graal.tranibor.net.PushRef

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class PushMessagesTest {
    private fun payload(kind: String, id: String = "x1", type: String = "action", org: String = "acme") =
        PushPayload(kind = kind, title = "Title", body = "Body", org = org, ref = PushRef(type, id), url = "/orgs/acme/x", at = "2026-11-19T10:00:00Z")

    @Test fun `the payload the server documents is read`() {
        val bytes = """{"kind":"decision.awaiting","title":"A decision","body":"deploy waits","org":"acme","ref":{"type":"action","id":"a1"},"url":"/orgs/acme/actions/a1","at":"2026-11-19T10:00:00Z"}""".encodeToByteArray()
        val p = PushMessages.parse(bytes)!!
        assertEquals("decision.awaiting", p.kind)
        assertEquals("a1", p.ref.id)
    }

    @Test fun `bytes that are not a payload are dropped`() {
        assertNull(PushMessages.parse("garbage".encodeToByteArray()))
        assertNull(PushMessages.parse("""{"title":"no kind"}""".encodeToByteArray()))
    }

    @Test fun `a decision leads to the inbox on that item`() {
        val link = PushMessages.linkFor(payload("decision.awaiting", "a1"))
        assertEquals("tranibor://decision/a1", link.toString())
        assertEquals(Destination.Inbox("a1"), PushMessages.destination(link))
    }

    @Test fun `a run and a session lead to the activity of their organisation`() {
        assertEquals(Destination.Activity("acme", "r1"), PushMessages.destination(PushMessages.linkFor(payload("run.ended", "r1", "run"))))
        assertEquals(Destination.Activity("acme", "s1"), PushMessages.destination(PushMessages.linkFor(payload("session.ended", "s1", "agent_session"))))
    }

    @Test fun `an agent's notification leads to the agent`() =
        assertEquals(Destination.Agent("acme", "bot"), PushMessages.destination(PushMessages.linkFor(payload("agent.notification", "bot", "agent"))))

    @Test fun `a kind this version does not know still opens the inbox`() =
        assertEquals(Destination.Inbox(), PushMessages.destination(PushMessages.linkFor(payload("something.new"))))

    @Test fun `a link that is not ours, or is malformed, opens nothing`() {
        assertNull(PushMessages.destination(Uri.parse("https://evil.example.com/decision/a1")))
        assertNull(PushMessages.destination(Uri.parse("tranibor://run/acme")))
        assertNull(PushMessages.destination(Uri.parse("tranibor://unknown/x")))
    }

    @Test fun `what is shown is bounded and cleaned`() {
        val shown = PushMessages.shown(payload("run.ended").copy(title = "T".repeat(500) + "\u001b[2J", body = "B".repeat(500)))
        assertTrue(shown.title.length <= 120)
        assertTrue(shown.text.length <= 200)
        assertEquals("Tranibor", PushMessages.shown(payload("run.ended").copy(title = " ")).title)
    }
}
