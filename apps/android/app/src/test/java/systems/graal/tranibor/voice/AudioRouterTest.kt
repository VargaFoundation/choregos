package systems.graal.tranibor.voice

import android.media.AudioDeviceInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AudioRouterTest {
    @Test fun `a Bluetooth headset wins over the phone's speaker`() =
        assertEquals(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioRouter.choose(listOf(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_BLUETOOTH_SCO)))

    @Test fun `a BLE headset wins over classic Bluetooth`() =
        assertEquals(AudioDeviceInfo.TYPE_BLE_HEADSET, AudioRouter.choose(listOf(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET)))

    @Test fun `a wired headset wins over the speaker`() =
        assertEquals(AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioRouter.choose(listOf(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_WIRED_HEADSET)))

    @Test fun `with nothing usable there is no choice to make`() = assertNull(AudioRouter.choose(listOf(AudioDeviceInfo.TYPE_TELEPHONY)))
}
