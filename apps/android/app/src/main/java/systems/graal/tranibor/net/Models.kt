package systems.graal.tranibor.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/** The JSON of the API: unknown members are ignored (the server grows faster than the app). */
val ApiJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = true
}

@Serializable
data class DeviceStartRequest(
    @SerialName("client_kind") val clientKind: String,
    @SerialName("client_name") val clientName: String,
    val org: String? = null,
)

@Serializable
data class DeviceAuthorization(
    @SerialName("device_code") val deviceCode: String,
    @SerialName("user_code") val userCode: String,
    @SerialName("verification_uri") val verificationUri: String,
    @SerialName("verification_uri_complete") val verificationUriComplete: String,
    @SerialName("expires_in") val expiresIn: Int,
    val interval: Int,
)

@Serializable
data class DeviceTokenRequest(
    @SerialName("grant_type") val grantType: String = GRANT,
    @SerialName("device_code") val deviceCode: String,
) {
    companion object {
        const val GRANT = "urn:ietf:params:oauth:grant-type:device_code"
    }
}

@Serializable
data class DeviceToken(
    @SerialName("access_token") val accessToken: String,
    @SerialName("session_id") val sessionId: String,
    val kind: String,
    @SerialName("expires_in") val expiresIn: Int,
    @SerialName("auth_time") val authTime: String? = null,
)

@Serializable
data class DeviceTokenError(val error: String? = null, val interval: Int? = null)

@Serializable
data class Org(val slug: String, val name: String, val role: String? = null)

@Serializable
data class AgentSocial(
    val watchers: Int? = null,
    val stars: Int? = null,
    val watching: String? = null,
    @SerialName("starred_by_me") val starredByMe: Boolean? = null,
)

@Serializable
data class AgentPermissions(val manage: Boolean? = null)

@Serializable
data class Agent(
    val slug: String,
    val kind: String,
    @SerialName("display_name") val displayName: String,
    val description: String? = null,
    val status: String,
    val lifecycle: String? = null,
    val project: String? = null,
    @SerialName("latest_version") val latestVersion: Int,
    @SerialName("last_run_at") val lastRunAt: String? = null,
    val can: AgentPermissions? = null,
    val social: AgentSocial? = null,
)

/** `spec.runtime` of an agent version: what the phone needs to know is only `kind` (and the voice's audio). */
@Serializable
data class RuntimeSpec(
    val kind: String = "prompt",
    val audio: VoiceAudio? = null,
    val greeting: String? = null,
    @SerialName("max_session_minutes") val maxSessionMinutes: Int? = null,
)

@Serializable
data class VoiceAudio(@SerialName("sample_rate") val sampleRate: Int? = null)

@Serializable
data class AgentSpec(val runtime: RuntimeSpec? = null)

@Serializable
data class AgentVersion(val version: Int, val spec: AgentSpec = AgentSpec())

@Serializable
data class StopRequest(val reason: String)

@Serializable
data class AgentStopResult(
    val slug: String,
    @SerialName("runs_cancelled") val runsCancelled: Int = 0,
    @SerialName("sessions_closed") val sessionsClosed: Int = 0,
    @SerialName("turns_cancelled") val turnsCancelled: Int = 0,
)

@Serializable
data class WatchPut(val level: String = "all")

@Serializable
data class MyAssistant(
    val org: String,
    val slug: String,
    val name: String,
    @SerialName("display_name") val displayName: String? = null,
    val description: String? = null,
    val status: String,
)

@Serializable
data class MyAssistantList(val items: List<MyAssistant> = emptyList())

@Serializable
data class ConversationSummary(
    val id: String,
    val title: String? = null,
    val agent: String,
    val org: String? = null,
    val assistant: String? = null,
    val status: String,
    val turns: Int = 0,
    @SerialName("cost_usd") val costUsd: Double = 0.0,
    @SerialName("last_activity_at") val lastActivityAt: String = "",
)

@Serializable
data class ConversationPage(val items: List<ConversationSummary> = emptyList())

@Serializable
data class ConversationMessage(val role: String, val content: String)

@Serializable
data class Conversation(
    val id: String,
    val title: String? = null,
    val agent: String,
    val org: String? = null,
    val assistant: String? = null,
    val status: String,
    val messages: List<ConversationMessage> = emptyList(),
)

@Serializable
data class AssistantConversationOpen(val context: JsonObject? = null)

@Serializable
data class ConversationMessageCreate(val input: String, val stream: Boolean = true)

@Serializable
data class AgentResponseCreate(
    val project: String,
    val input: String,
    @SerialName("session_id") val sessionId: String? = null,
    val stream: Boolean = true,
)

@Serializable
data class AgentPendingAction(val id: String, val summary: String, @SerialName("confirm_url") val confirmUrl: String)

@Serializable
data class AgentResponseError(val code: String = "", val message: String = "")

/** The `response.*` events of a stream carry this (`response.created`, `.completed`, `.failed`). */
@Serializable
data class AgentResponse(
    val id: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val status: String = "completed",
    @SerialName("output_text") val outputText: String? = null,
    val error: AgentResponseError? = null,
    @SerialName("pending_actions") val pendingActions: List<AgentPendingAction> = emptyList(),
)

@Serializable
data class AgentDelta(val delta: String = "")

@Serializable
data class SpendSoFar(@SerialName("cost_usd") val costUsd: Double = 0.0)

@Serializable
data class ActivityFailure(val reason: String = "", val summary: String = "")

@Serializable
data class ActivityEntry(
    val kind: String,
    val id: String,
    val status: String,
    @SerialName("project_slug") val projectSlug: String? = null,
    @SerialName("work_item_title") val workItemTitle: String? = null,
    val workflow: String? = null,
    @SerialName("agent_slug") val agentSlug: String? = null,
    val assistant: String? = null,
    val model: String? = null,
    @SerialName("started_at") val startedAt: String = "",
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("last_event") val lastEvent: String? = null,
    val spend: SpendSoFar? = null,
    @SerialName("can_stop") val canStop: Boolean = false,
    val failure: ActivityFailure? = null,
)

@Serializable
data class ActivityList(
    @SerialName("in_flight") val inFlight: List<ActivityEntry> = emptyList(),
    val finished: List<ActivityEntry> = emptyList(),
)

@Serializable
data class SurfaceSession(
    val id: String,
    val kind: String,
    @SerialName("client_name") val clientName: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    val ip: String? = null,
    val current: Boolean = false,
)

@Serializable
data class PendingDecision(val method: String = "POST", val path: String, val url: String = "")

@Serializable
data class PendingWhere(
    val org: String? = null,
    @SerialName("project_slug") val projectSlug: String? = null,
    @SerialName("work_item_key") val workItemKey: String? = null,
    @SerialName("work_item_id") val workItemId: String? = null,
    @SerialName("conversation_id") val conversationId: String? = null,
)

@Serializable
data class PendingItem(
    val id: String,
    val kind: String,
    val what: String,
    val since: String = "",
    @SerialName("due_at") val dueAt: String? = null,
    val where: PendingWhere? = null,
    val decision: PendingDecision,
)

@Serializable
data class PendingList(val items: List<PendingItem> = emptyList())


// ───────────── Web Push (S81-02, ADR 0194) ─────────────

@Serializable
data class PushVapidKey(@SerialName("public_key") val publicKey: String)

@Serializable
data class PushKeys(val p256dh: String, val auth: String)

@Serializable
data class PushSubscriptionCreate(
    val endpoint: String,
    val keys: PushKeys,
    @SerialName("device_name") val deviceName: String,
    val kinds: List<String>? = null,
)

@Serializable
data class PushSubscription(
    val id: String,
    @SerialName("device_name") val deviceName: String,
    @SerialName("endpoint_host") val endpointHost: String = "",
    val kinds: List<String> = emptyList(),
    @SerialName("last_delivered_at") val lastDeliveredAt: String? = null,
    @SerialName("last_failure_at") val lastFailureAt: String? = null,
)

@Serializable
data class PushTestResult(
    val delivered: Boolean,
    @SerialName("status_code") val statusCode: Int? = null,
    val removed: Boolean = false,
)

@Serializable
data class PushRef(val type: String = "none", val id: String = "")

/** What a device receives (RFC 8291 message, decrypted by the UnifiedPush connector). */
@Serializable
data class PushPayload(
    val kind: String,
    val title: String = "",
    val body: String = "",
    val org: String = "",
    val ref: PushRef = PushRef(),
    val url: String = "",
    val at: String = "",
)
