package systems.graal.tranibor.push

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.unifiedpush.android.connector.UnifiedPush
import systems.graal.tranibor.KeyValue
import systems.graal.tranibor.net.ApiClient
import systems.graal.tranibor.net.ApiException
import systems.graal.tranibor.net.PushKeys
import systems.graal.tranibor.net.PushSubscriptionCreate

/** The calls into the UnifiedPush connector, behind an interface so the registration logic is testable without a distributor. */
interface PushPlatform {
    /** Package names of the installed distributors (ntfy, ...), the app's own excluded. */
    fun distributors(): List<String>
    fun register(distributor: String, vapid: String)
    fun unregister()
}

class UnifiedPushPlatform(private val context: Context) : PushPlatform {
    override fun distributors(): List<String> = UnifiedPush.getDistributors(context).filter { it != context.packageName }

    override fun register(distributor: String, vapid: String) {
        UnifiedPush.saveDistributor(context, distributor)
        UnifiedPush.register(context, messageForDistributor = "Tranibor", vapid = vapid)
    }

    override fun unregister() = UnifiedPush.unregister(context)
}

data class PushState(
    val distributors: List<String> = emptyList(),
    /** The server-side id of this device's subscription, once the endpoint is registered. */
    val subscriptionId: String? = null,
    /** Waiting for the distributor to hand over an endpoint. */
    val registering: Boolean = false,
    val message: String? = null,
)

/**
 * Tells the instance where this device can be reached (S81-06, ADR 0194): the distributor hands an endpoint and a Web Push
 * key pair, and the app registers them against `POST /me/push-subscriptions`. The server encrypts each message
 * for those keys (RFC 8291); only this device, which holds the private key inside the connector, can read it.
 */
class PushRegistrar(
    private val api: () -> ApiClient?,
    private val scope: CoroutineScope,
    private val platform: PushPlatform,
    private val store: KeyValue,
    private val deviceName: String,
) {
    private val _state = MutableStateFlow(PushState(distributors = platform.distributors(), subscriptionId = store.get(SUB)))
    val state: StateFlow<PushState> = _state.asStateFlow()

    /** Re-reads which distributors are installed (the person may have just installed ntfy). */
    fun refresh() = _state.update { it.copy(distributors = platform.distributors(), subscriptionId = store.get(SUB)) }

    /** Turns notifications on through [distributor]: the instance's VAPID key first, then the connector's registration. */
    fun enable(distributor: String) {
        scope.launch {
            val client = api() ?: return@launch
            _state.update { it.copy(registering = true, message = null) }
            try {
                val vapid = client.vapidKey()
                platform.register(distributor, vapid)
            } catch (e: ApiException) {
                _state.update {
                    it.copy(
                        registering = false,
                        message = if (e.status == 404 || e.status == 409) "This instance has no push notifications set up." else e.message,
                    )
                }
            } catch (e: Exception) {
                _state.update { it.copy(registering = false, message = e.message ?: "Could not reach the instance.") }
            }
        }
    }

    /** The distributor handed an endpoint (or a new one): the instance learns it. Idempotent on the endpoint. */
    fun endpointChanged(endpoint: String, p256dh: String, auth: String) {
        scope.launch {
            val client = api() ?: return@launch
            try {
                val subscription = client.registerPush(
                    PushSubscriptionCreate(endpoint, PushKeys(p256dh, auth), deviceName.take(80).ifBlank { "Android" }),
                )
                store.put(SUB, subscription.id)
                _state.update { it.copy(subscriptionId = subscription.id, registering = false, message = null) }
            } catch (e: Exception) {
                _state.update { it.copy(registering = false, message = "The instance refused this device: ${e.message}") }
            }
        }
    }

    fun registrationFailed(reason: String) =
        _state.update { it.copy(registering = false, message = "The push service could not register this device ($reason).") }

    /** The distributor was uninstalled or dropped the registration: the instance stops telling a device that cannot hear. */
    fun unregisteredByDistributor() {
        disable(callPlatform = false)
    }

    fun disable(callPlatform: Boolean = true) {
        val id = store.get(SUB)
        store.put(SUB, null)
        _state.update { it.copy(subscriptionId = null, registering = false) }
        if (callPlatform) platform.unregister()
        if (id != null) scope.launch { runCatching { api()?.deletePush(id) } }
    }

    /** Asks the instance to push a test message to this device and says what came of it. */
    fun sendTest() {
        val id = store.get(SUB) ?: return
        scope.launch {
            val client = api() ?: return@launch
            try {
                val result = client.testPush(id)
                _state.update {
                    it.copy(
                        message = when {
                            result.removed -> "The push service says this device is gone. Turn notifications on again."
                            result.delivered -> "Sent. It should arrive in a few seconds."
                            else -> "The push service did not accept it (${result.statusCode ?: "no answer"})."
                        },
                        subscriptionId = if (result.removed) null else it.subscriptionId,
                    )
                }
                if (result.removed) store.put(SUB, null)
            } catch (e: Exception) {
                _state.update { it.copy(message = e.message) }
            }
        }
    }

    /** A test message arrived: the loop is closed. */
    fun testArrived() = _state.update { it.copy(message = "The test notification arrived on this device.") }

    companion object {
        private const val SUB = "subscription"
    }
}
