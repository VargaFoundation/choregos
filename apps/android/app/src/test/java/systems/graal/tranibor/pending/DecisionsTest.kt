package systems.graal.tranibor.pending

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.graal.tranibor.net.PendingDecision
import systems.graal.tranibor.net.PendingItem
import systems.graal.tranibor.net.PendingWhere
import systems.graal.tranibor.pending.Decisions.Verb

class DecisionsTest {
    private fun item(kind: String, path: String, where: PendingWhere = PendingWhere(org = "acme", conversationId = "c1", workItemId = "w1")) =
        PendingItem("a1", kind, "do the thing", where = where, decision = PendingDecision(path = path))

    @Test fun `each kind finds its record where the CLI looks`() {
        assertEquals("/me/conversations/c1/actions", Decisions.detailPath(item("conversation_action", "/x")))
        assertEquals("/me/conversations/c1/actions", Decisions.detailPath(item("launch", "/x")))
        assertEquals("/orgs/acme/actions/a1", Decisions.detailPath(item("governed_action", "/orgs/acme/actions/a1/decision")))
        assertEquals("/work-items/w1", Decisions.detailPath(item("human_step", "/x")))
    }

    @Test fun `a record that is not the item's is not waiting any more`() {
        val it = item("governed_action", "/orgs/acme/actions/a1/decision")
        assertNull(Decisions.pickDetail(it, buildJsonObject { put("id", "other") }))
        assertEquals("a1", Decisions.pickDetail(it, buildJsonObject { put("id", "a1") })?.get("id")?.toString()?.trim('"'))
    }

    @Test fun `an assistant's write is confirmed with no body and refused with a reason`() {
        val it = item("conversation_action", "/me/conversations/c1/actions/a1/confirm")
        assertEquals("/me/conversations/c1/actions/a1/confirm" to null, Decisions.requestFor(it, Verb.Approve))
        val (path, body) = Decisions.requestFor(it, Verb.Reject, reason = "no")
        assertEquals("/me/conversations/c1/actions/a1/refuse", path)
        assertEquals("no", body!!["reason"].toString().trim('"'))
    }

    @Test fun `a governed action carries the verb and the reason, or an explicit null`() {
        val it = item("governed_action", "/orgs/acme/actions/a1/decision")
        val approved = Decisions.requestFor(it, Verb.Approve).second!!
        assertEquals("approve", approved["decision"].toString().trim('"'))
        assertEquals(JsonNull, approved["reason"])
        assertEquals("too risky", Decisions.requestFor(it, Verb.Reject, reason = "too risky").second!!["reason"].toString().trim('"'))
    }

    @Test fun `a human step is answered or decided by request id`() {
        val it = item("human_step", "/work-items/w1/pending-request/decision")
        val answered = Decisions.requestFor(it, Verb.Answer, answer = "blue").second!!
        assertEquals("answer", answered["kind"].toString().trim('"'))
        assertEquals("blue", answered["answer"].toString().trim('"'))
        assertEquals("a1", answered["request_id"].toString().trim('"'))
    }

    @Test fun `only an approval or a question is decided from the phone`() {
        val it = item("human_step", "/x")
        fun detail(kind: String) = buildJsonObject { put("kind", kind) }
        assertEquals(listOf(Verb.Approve, Verb.Reject), Decisions.verbs(it, detail("approval")))
        assertEquals(listOf(Verb.Answer), Decisions.verbs(it, detail("question")))
        assertTrue(Decisions.verbs(it, detail("task")).isEmpty())
        assertTrue(Decisions.verbs(it, null).isEmpty())
    }

    @Test fun `a rejection asks a reason and an approval does not`() {
        assertTrue(Decisions.asksReason(Verb.Reject))
        assertFalse(Decisions.asksReason(Verb.Approve))
    }

    @Test fun `text from the platform cannot move the screen`() {
        val dirty = "ok\u001b[2Jdone\u0007‮"
        assertFalse(Decisions.clean(dirty).any { it.isISOControl() || it == '‮' })
    }

    @Test fun `a long argument is cut and says so`() {
        val it = item("governed_action", "/orgs/acme/actions/a1/decision")
        val detail = buildJsonObject {
            put("id", "a1"); put("kind", "k"); put("origin", "o")
            put("params", buildJsonObject { put("blob", "x".repeat(Decisions.MAX_VALUE * 2)) })
        }
        val shown = Decisions.describe(it, detail).first { it.contains("blob") }
        assertTrue(shown.length < Decisions.MAX_VALUE + 100)
        assertTrue(shown.contains("more") || shown.contains("cut") || shown.contains("…") || shown.contains("..."))
    }

    @Test fun `what the person reads includes the arguments and the effects`() {
        val it = item("governed_action", "/orgs/acme/actions/a1/decision")
        val detail: JsonObject = buildJsonObject {
            put("id", "a1"); put("kind", "delete"); put("origin", "agent")
            put("params", buildJsonObject { put("target", "prod-db") })
            put("effects", buildJsonArray { add(kotlinx.serialization.json.JsonPrimitive("drops 3 tables")) })
        }.jsonObject
        val text = Decisions.describe(it, detail).joinToString("\n")
        assertTrue(text.contains("prod-db"))
        assertTrue(text.contains("drops 3 tables"))
    }
}
