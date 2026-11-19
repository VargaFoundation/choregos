package systems.graal.tranibor.voice

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmTest {
    @Test fun `20 ms at 24 kHz is 960 bytes of whole samples`() = assertEquals(960, Pcm.bytesFor(20))

    @Test fun `base64 round-trips PCM`() {
        val pcm = ByteArray(480) { (it * 7).toByte() }
        assertArrayEquals(pcm, Pcm.decode(Pcm.encode(pcm)))
    }

    @Test fun `a trailing odd byte is dropped so a sample is never cut in half`() {
        assertEquals(2, Pcm.decode(Pcm.encode(byteArrayOf(1, 2, 3)))!!.size)
    }

    @Test fun `text that is not base64 is not audio`() = assertNull(Pcm.decode("not base64 !!"))

    @Test fun `silence has no level and a full-scale square wave has all of it`() {
        assertEquals(0f, Pcm.level(ByteArray(960)), 0f)
        val loud = ByteArray(960) { if (it % 2 == 1) 0x7F else 0xFF.toByte() }
        assertTrue(Pcm.level(loud) > 0.95f)
    }

    @Test fun `the peak is read as signed little-endian`() {
        // -2 as a little-endian short is FE FF; its magnitude is 2.
        assertEquals(2, Pcm.peak(byteArrayOf(0xFE.toByte(), 0xFF.toByte())))
    }

    @Test fun `the framer cuts any read into frames of one size and keeps the rest`() {
        val framer = Framer(960)
        assertEquals(0, framer.push(ByteArray(500)).size)
        val frames = framer.push(ByteArray(1500))
        assertEquals(2, frames.size)
        assertTrue(frames.all { it.size == 960 })
        assertEquals(2000 - 1920, framer.pending)
    }

    @Test fun `the framer keeps the order of the bytes across reads`() {
        val framer = Framer(4)
        val out = framer.push(byteArrayOf(1, 2, 3)) + framer.push(byteArrayOf(4, 5, 6, 7, 8))
        assertArrayEquals(byteArrayOf(1, 2, 3, 4), out[0])
        assertArrayEquals(byteArrayOf(5, 6, 7, 8), out[1])
    }

    @Test fun `only the first bytes of a short read are used`() {
        assertEquals(1, Framer(4).push(ByteArray(100), length = 4).size)
    }
}
