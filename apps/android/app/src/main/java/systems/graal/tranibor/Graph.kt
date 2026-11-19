package systems.graal.tranibor

import android.content.Context
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import systems.graal.tranibor.auth.AesGcmBox
import systems.graal.tranibor.auth.DeviceSignIn
import systems.graal.tranibor.auth.PrefsBlobStore
import systems.graal.tranibor.auth.Session
import systems.graal.tranibor.auth.SessionStore
import systems.graal.tranibor.net.ApiClient

/** What the app holds for its whole life: the session, the client built on it. */
class Graph(private val context: Context) {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private val store = SessionStore(AesGcmBox.keystore("tranibor.session"), PrefsBlobStore(context))
    private val _session = MutableStateFlow(store.load())

    /** The signed-in session, or `null`. */
    val session: StateFlow<Session?> = _session.asStateFlow()

    private val deviceName = listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.joinToString(" ").take(80)

    /** The client of the signed-in instance, or `null` when signed out. */
    fun api(): ApiClient? = _session.value?.let { s -> ApiClient(s.origin, { _session.value?.token }) }

    /** A client for an instance the person is still signing in to (no token). */
    fun signInFor(origin: String): DeviceSignIn = DeviceSignIn(ApiClient(origin, { null }), "Tranibor for Android ($deviceName)")

    fun signedIn(session: Session) {
        store.save(session)
        _session.value = session
    }

    /** Forgets the session on this device (the caller ends it on the server first, when it can). */
    fun signedOut() {
        store.clear()
        _session.value = null
    }

    /** Small per-device choices (the organisation last used). Not secrets. */
    val prefs = PrefsKeyValue(context, "tranibor.ui")
}
