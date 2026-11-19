package systems.graal.tranibor.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class InstanceTest {
    @Test fun `a bare host becomes an https origin`() = assertEquals("https://tranibor.example.com", Instance.normalise("Tranibor.Example.com"))

    @Test fun `a path, the default port and a trailing slash are dropped`() =
        assertEquals("https://tranibor.example.com", Instance.normalise("https://tranibor.example.com:443/console/"))

    @Test fun `another port is kept`() = assertEquals("https://t.example.com:8443", Instance.normalise("t.example.com:8443"))

    @Test fun `clear text is refused and not upgraded`() {
        assertThrows(Instance.Invalid::class.java) { Instance.normalise("http://tranibor.example.com") }
    }

    @Test fun `credentials in the address are refused`() {
        assertThrows(Instance.Invalid::class.java) { Instance.normalise("https://me:secret@tranibor.example.com") }
    }

    @Test fun `an empty address is refused`() {
        assertThrows(Instance.Invalid::class.java) { Instance.normalise("   ") }
    }

    @Test fun `the websocket address of a route`() =
        assertEquals("wss://t.example.com/api/v1/orgs/a/agents/b/realtime", Instance.websocket("https://t.example.com", "/orgs/a/agents/b/realtime"))
}
