package systems.graal.tranibor.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class CloseNoticeTest {
    @Test fun `a normal close is a quiet end`() = assertEquals(EndKind.Normal, CloseNotice.of(1000, "").kind)

    @Test fun `the agent's time cap is said as a time limit`() {
        val n = CloseNotice.of(1000, "max_session_minutes reached")
        assertEquals(EndKind.TimeCap, n.kind)
        assertEquals("The time limit of this agent's voice sessions is reached.", n.title)
    }

    @Test fun `a spent budget is said as a spending limit, not as a code`() {
        val n = CloseNotice.of(1008, "session budget spent")
        assertEquals(EndKind.SpendCap, n.kind)
        assertFalse(n.title.contains("1008"))
    }

    @Test fun `any other 1008 is a refusal that carries the relay's reason`() {
        val n = CloseNotice.of(1008, "not allowed")
        assertEquals(EndKind.Refused, n.kind)
        assertEquals("not allowed", n.detail)
    }

    @Test fun `the other codes the relay uses`() {
        assertEquals(EndKind.TooLarge, CloseNotice.of(1009, "").kind)
        assertEquals(EndKind.ServerError, CloseNotice.of(1011, "").kind)
        assertEquals(EndKind.Busy, CloseNotice.of(1013, "").kind)
        assertEquals(EndKind.Dropped, CloseNotice.of(1001, "").kind)
        assertEquals(EndKind.Dropped, CloseNotice.of(4999, "").kind)
    }
}
