package systems.graal.tranibor.net

import java.net.URI

/** An instance URL as a person types it: `tranibor.example.com`, `https://tranibor.example.com/`. */
object Instance {
    class Invalid(message: String) : IllegalArgumentException(message)

    /**
     * The origin to talk to, `https://host[:port]`, no path, no trailing slash. A token sent over clear text is
     * a stolen token, so only `https` is accepted (the manifest's network security config refuses clear text
     * as well): a typed `http://` is an error, not a silent upgrade that would hide a wrong address.
     */
    fun normalise(typed: String): String {
        val text = typed.trim()
        if (text.isEmpty()) throw Invalid("Type the address of your Tranibor instance.")
        val withScheme = if ("://" in text) text else "https://$text"
        val uri = try {
            URI(withScheme)
        } catch (_: Exception) {
            throw Invalid("This is not an address.")
        }
        if (uri.scheme != "https") throw Invalid("Only https addresses are accepted.")
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty()) throw Invalid("This is not an address.")
        if (uri.userInfo != null) throw Invalid("An address carries no user name or password.")
        val port = if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
        val shown = if (":" in host) "[$host]" else host
        return "https://$shown$port"
    }

    /** `<origin>/api/v1`: where every route of the API lives. */
    fun apiBase(origin: String): String = "$origin/api/v1"

    /** The WebSocket address of a route: `https` becomes `wss`. */
    fun websocket(origin: String, path: String): String = origin.replaceFirst("https://", "wss://") + "/api/v1" + path
}
