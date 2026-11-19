package systems.graal.tranibor.net

import okio.BufferedSource

/** One server-sent event (WHATWG HTML §9.2): its name, its data (lines joined with `\n`) and its id. */
data class SseEvent(val event: String, val data: String, val id: String?)

/**
 * Reads `text/event-stream` from [source] until it ends, calling [onEvent] for each event. Comments
 * (`: ping`) are dropped; `event` defaults to `message`; a `data` field may span lines; LF and CRLF both
 * end a line. An event without data is not dispatched (the standard says so).
 */
fun readSse(source: BufferedSource, onEvent: (SseEvent) -> Unit) {
    var name = ""
    var id: String? = null
    val data = StringBuilder()
    var hasData = false
    while (true) {
        val line = source.readUtf8Line() ?: break
        if (line.isEmpty()) {
            if (hasData) onEvent(SseEvent(name.ifEmpty { "message" }, data.toString(), id))
            name = ""
            data.setLength(0)
            hasData = false
            continue
        }
        if (line.startsWith(":")) continue
        val colon = line.indexOf(':')
        val field = if (colon < 0) line else line.substring(0, colon)
        var value = if (colon < 0) "" else line.substring(colon + 1)
        if (value.startsWith(" ")) value = value.substring(1)
        when (field) {
            "event" -> name = value
            "data" -> {
                if (hasData) data.append('\n')
                data.append(value)
                hasData = true
            }
            "id" -> if ('\u0000' !in value) id = value
        }
    }
    // A stream cut in the middle of an event delivers nothing of it: half an event is not an event.
}
