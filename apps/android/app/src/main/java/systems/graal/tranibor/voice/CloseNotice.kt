package systems.graal.tranibor.voice

/** Why a voice session ended, in words a person understands (never a bare close code). */
enum class EndKind { Normal, TimeCap, SpendCap, Busy, TooLarge, Refused, Dropped, ServerError, Unreachable }

data class CloseNotice(val kind: EndKind, val title: String, val detail: String? = null) {
    companion object {
        /**
         * What the relay's close means (see `agent_realtime`): 1000 ends normally (the agent's time cap included),
         * 1008 refuses (a spent budget, a right the person lacks, a resume that is no longer held), 1009 is a frame
         * too large, 1011 a relay failure, 1013 another process holds the session, 1001 the caller was gone too long.
         */
        fun of(code: Int, reason: String): CloseNotice {
            val why = reason.trim().takeIf { it.isNotEmpty() }
            return when {
                code == 1000 && why?.contains("max_session_minutes") == true ->
                    CloseNotice(EndKind.TimeCap, "The time limit of this agent's voice sessions is reached.", why)
                code == 1000 -> CloseNotice(EndKind.Normal, "The session ended.", null)
                code == 1008 && (why?.contains("budget", true) == true || why?.contains("spent", true) == true) ->
                    CloseNotice(EndKind.SpendCap, "The spending limit of this session is reached.", why)
                code == 1008 -> CloseNotice(EndKind.Refused, "The session was refused.", why)
                code == 1009 -> CloseNotice(EndKind.TooLarge, "A message was too large for the relay.", why)
                code == 1011 -> CloseNotice(EndKind.ServerError, "The voice relay failed.", why)
                code == 1013 -> CloseNotice(EndKind.Busy, "The relay is busy. Try again in a moment.", why)
                code == 1001 -> CloseNotice(EndKind.Dropped, "The connection was lost for too long and the session closed.", why)
                else -> CloseNotice(EndKind.Dropped, "The session closed (code $code).", why)
            }
        }

        val unreachable = CloseNotice(EndKind.Unreachable, "The instance cannot be reached.")
        val lostConnection = CloseNotice(EndKind.Dropped, "The connection was lost and could not be restored in time.")
    }
}
