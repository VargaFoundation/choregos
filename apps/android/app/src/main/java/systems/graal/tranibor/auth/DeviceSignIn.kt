package systems.graal.tranibor.auth

import kotlinx.coroutines.delay
import systems.graal.tranibor.net.ApiClient
import systems.graal.tranibor.net.DeviceAuthorization
import systems.graal.tranibor.net.DeviceToken

/** The sign-in did not complete; [message] says why in words a person reads. */
class SignInFailed(message: String) : Exception(message)

/**
 * The device flow of RFC 8628, brokered by the console (ADR 0154 section 6): the app asks for two codes, shows the
 * short one and a link, and polls while the person approves it in the console, signed in as themselves.
 * What comes back is a session of the person's own, never an API token.
 */
class DeviceSignIn(
    private val api: ApiClient,
    private val clientName: String,
    private val now: () -> Long = System::currentTimeMillis,
    private val wait: suspend (Long) -> Unit = { delay(it) },
) {
    suspend fun start(): DeviceAuthorization = api.startDevice(clientName)

    /** The link to open: the code is in it, so nothing is retyped. [fresh] makes the console re-authenticate the person first (ADR 0170). */
    fun link(codes: DeviceAuthorization, fresh: Boolean): String =
        codes.verificationUriComplete + if (fresh) (if ('?' in codes.verificationUriComplete) "&fresh=1" else "?fresh=1") else ""

    /** Polls until the person decides. Honours `interval` and `slow_down`; gives up when the code expires. */
    suspend fun await(codes: DeviceAuthorization): DeviceToken {
        var interval = codes.interval.coerceAtLeast(1).toLong()
        val deadline = now() + codes.expiresIn * 1000L
        while (now() < deadline) {
            wait(interval * 1000)
            when (val polled = api.pollDevice(codes.deviceCode)) {
                is ApiClient.PollResult.Granted -> return polled.token
                is ApiClient.PollResult.Pending -> when (polled.error) {
                    "authorization_pending" -> Unit
                    "slow_down" -> interval = (polled.interval ?: (interval + 5)).toLong()
                    "access_denied" -> throw SignInFailed("The sign-in was refused in the console.")
                    "expired_token" -> throw SignInFailed("The code expired before it was approved. Start again.")
                    else -> throw SignInFailed("Unexpected answer while waiting (${polled.error}).")
                }
            }
        }
        throw SignInFailed("The code expired before it was approved. Start again.")
    }
}
