package systems.graal.tranibor.net

/**
 * An answer that is not a success. `codes` gathers every machine code of the problem document
 * (`code`, and `errors[].error`): the app reacts to `step_up_required` and `decision_requires_session`.
 */
class ApiException(
    val status: Int,
    val title: String,
    val detail: String,
    val codes: Set<String> = emptySet(),
) : Exception(if (detail.isNotBlank()) detail else title) {
    val needsFreshSignIn: Boolean get() = status == 401 && "step_up_required" in codes
    val needsOwnSession: Boolean get() = "decision_requires_session" in codes

    /** A 401 that is not a step-up: the session is gone (ended, expired, revoked). */
    val sessionEnded: Boolean get() = status == 401 && !needsFreshSignIn
}

/** The network could not be reached, or answered nothing readable. */
class NetworkException(message: String, cause: Throwable? = null) : Exception(message, cause)
