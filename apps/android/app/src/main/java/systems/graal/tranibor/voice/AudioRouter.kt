package systems.graal.tranibor.voice

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build

/**
 * Where a voice session's sound goes. A headset (Bluetooth, wired, USB) wins when one is connected, so a call
 * continues in the person's ear; otherwise the loudspeaker, because the earpiece is wrong for an agent the person
 * talks to at arm's length. The session runs in communication mode, which is what lets echo cancelling work.
 */
class AudioRouter(context: Context) {
    private val manager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var previousMode = AudioManager.MODE_NORMAL
    private var scoStarted = false

    fun acquire() {
        previousMode = manager.mode
        manager.mode = AudioManager.MODE_IN_COMMUNICATION
        val devices = manager.availableCommunicationDevicesCompat()
        val chosen = choose(devices.map { it.type })
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            devices.firstOrNull { it.type == chosen }?.let { manager.setCommunicationDevice(it) }
        } else {
            @Suppress("DEPRECATION")
            if (chosen == AudioDeviceInfo.TYPE_BLUETOOTH_SCO) {
                manager.startBluetoothSco()
                scoStarted = true
            } else {
                manager.isSpeakerphoneOn = chosen == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            }
        }
    }

    fun release() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            manager.clearCommunicationDevice()
        } else {
            @Suppress("DEPRECATION")
            if (scoStarted) manager.stopBluetoothSco()
            @Suppress("DEPRECATION")
            manager.isSpeakerphoneOn = false
        }
        scoStarted = false
        manager.mode = previousMode
    }

    private fun AudioManager.availableCommunicationDevicesCompat(): List<AudioDeviceInfo> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) availableCommunicationDevices
        else getDevices(AudioManager.GET_DEVICES_OUTPUTS).toList()

    companion object {
        /** In order of preference. A hearing aid is a headset for this purpose. */
        private val PREFERENCE = listOf(
            AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_HEARING_AID,
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_USB_HEADSET,
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
        )

        /** The device type to talk through among the connected [types], or `null` when none is usable. */
        fun choose(types: List<Int>): Int? = PREFERENCE.firstOrNull { it in types }
    }
}
