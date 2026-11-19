package systems.graal.tranibor.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import systems.graal.tranibor.MainActivity
import systems.graal.tranibor.R
import systems.graal.tranibor.TraniborApp

/**
 * A foreground service of type `microphone`: a voice session goes on with the screen off or the app in the
 * background, and the person sees, in the notification shade, that the microphone is open and can end it there.
 */
class VoiceService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val voice = (application as TraniborApp).graph.voice
        if (intent?.action == ACTION_END) {
            voice.stop()
            return START_NOT_STICKY
        }
        ensureChannel()
        startForeground(NOTIFICATION_ID, notification(voice.config?.title), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        // The socket and the audio must keep running with the screen off: hold the CPU, for the session's life only.
        wake = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "tranibor:voice").apply { setReferenceCounted(false); acquire(MAX_WAKE_MS) }
        voice.begin()
        scope.launch {
            voice.state.collect {
                if (it == null || it.phase == Phase.Ended) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        wake?.takeIf { it.isHeld }?.release()
        scope.cancel()
        // A service killed with a session running: the audio must not stay open.
        (application as TraniborApp).graph.voice.stop()
        super.onDestroy()
    }

    private fun ensureChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.voice_channel_name), NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun notification(title: String?): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val end = PendingIntent.getService(
            this, 1, Intent(this, VoiceService::class.java).setAction(ACTION_END), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.voice_notification_title))
            .setContentText(title)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setContentIntent(open)
            .addAction(0, getString(R.string.voice_notification_end), end)
            .build()
    }

    companion object {
        const val CHANNEL = "voice"
        const val NOTIFICATION_ID = 81
        const val ACTION_END = "systems.graal.tranibor.voice.END"

        /** A session is capped by the agent's `max_session_minutes` and by its budget; this is only a backstop. */
        private const val MAX_WAKE_MS = 4 * 60 * 60 * 1000L
    }
}
