package systems.graal.tranibor.push

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage
import systems.graal.tranibor.MainActivity
import systems.graal.tranibor.R
import systems.graal.tranibor.TraniborApp

/**
 * What the UnifiedPush distributor (ntfy, ...) hands the app. The connector has already decrypted the message with the
 * device's own keys (RFC 8291): [onMessage] sees the clear `PushPayload` and nothing else. A message that does not
 * decrypt, or is not ours, is dropped without a notification: the push channel is a way to be told, not to be tricked.
 */
class PushServiceImpl : PushService() {
    private val graph get() = (application as TraniborApp).graph

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        val keys = endpoint.pubKeySet ?: return // an endpoint without Web Push keys cannot be decrypted: not registered
        if (endpoint.temporary) return // a fallback of a distributor that is down: the real one will call again
        graph.push.endpointChanged(endpoint.url, keys.pubKey, keys.auth)
    }

    override fun onMessage(message: PushMessage, instance: String) {
        if (!message.decrypted) return
        val payload = PushMessages.parse(message.content) ?: return
        if (payload.kind == "push.test") return graph.push.testArrived()
        Notifier.show(this, PushMessages.shown(payload), payload.kind)
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        graph.push.registrationFailed(reason.name)
    }

    override fun onUnregistered(instance: String) {
        graph.push.unregisteredByDistributor()
    }
}

/** Shows the notifications of the app. */
object Notifier {
    const val CHANNEL = "alerts"

    fun ensureChannel(context: Context) {
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, context.getString(R.string.alerts_channel_name), NotificationManager.IMPORTANCE_HIGH),
        )
    }

    fun show(context: Context, shown: Shown, kind: String) {
        ensureChannel(context)
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val open = PendingIntent.getActivity(
            context,
            shown.link.hashCode(),
            Intent(Intent.ACTION_VIEW, shown.link, context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(shown.title)
            .setContentText(shown.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(shown.text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(if (kind == "decision.awaiting") NotificationCompat.CATEGORY_REMINDER else NotificationCompat.CATEGORY_STATUS)
            .build()
        NotificationManagerCompat.from(context).notify(shown.link.toString(), 0, notification)
    }
}
