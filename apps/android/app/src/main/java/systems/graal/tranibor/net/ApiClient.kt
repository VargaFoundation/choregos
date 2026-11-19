package systems.graal.tranibor.net

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Where the app stands against one instance: its origin and the session token, if any. */
class ApiClient(
    val origin: String,
    private val token: () -> String?,
    private val http: OkHttpClient = defaultHttp(),
) {
    private val base: HttpUrl = Instance.apiBase(origin).toHttpUrl()
    private val jsonType = "application/json".toMediaType()

    // ───────────── sign-in (no token yet) ─────────────

    suspend fun startDevice(clientName: String, org: String? = null): DeviceAuthorization =
        post("/auth/device", DeviceStartRequest.serializer(), DeviceStartRequest("cli", clientName, org), DeviceAuthorization.serializer(), auth = false)

    /** One poll: the session, or the RFC 8628 error to react to (`authorization_pending`, `slow_down`, ...). */
    suspend fun pollDevice(deviceCode: String): PollResult {
        val request = Request.Builder().url(url("/auth/device/token"))
            .post(ApiJson.encodeToString(DeviceTokenRequest.serializer(), DeviceTokenRequest(deviceCode = deviceCode)).toRequestBody(jsonType))
            .build()
        return call(request).use { response ->
            val text = response.body?.string().orEmpty()
            if (response.isSuccessful) {
                PollResult.Granted(ApiJson.decodeFromString(DeviceToken.serializer(), text))
            } else {
                val error = runCatching { ApiJson.decodeFromString(DeviceTokenError.serializer(), text) }.getOrNull()
                PollResult.Pending(error?.error ?: "unexpected_${response.code}", error?.interval)
            }
        }
    }

    sealed interface PollResult {
        data class Granted(val token: DeviceToken) : PollResult
        data class Pending(val error: String, val interval: Int?) : PollResult
    }

    // ───────────── reads ─────────────

    suspend fun orgs(): List<Org> = get("/orgs", ListSerializer(Org.serializer()))

    suspend fun agents(org: String): List<Agent> = get("/orgs/$org/agents", ListSerializer(Agent.serializer()))

    suspend fun agentVersion(org: String, slug: String, version: Int): AgentVersion =
        get("/orgs/$org/agents/$slug/versions/$version", AgentVersion.serializer())

    suspend fun assistants(): List<MyAssistant> = get("/me/assistants", MyAssistantList.serializer()).items

    suspend fun conversations(): List<ConversationSummary> = get("/me/conversations", ConversationPage.serializer()).items

    suspend fun conversation(id: String): Conversation = get("/me/conversations/$id", Conversation.serializer())

    suspend fun activity(org: String): ActivityList = get("/orgs/$org/activity", ActivityList.serializer())

    suspend fun sessions(): List<SurfaceSession> = get("/me/sessions", ListSerializer(SurfaceSession.serializer()))

    suspend fun pending(): List<PendingItem> = get("/me/pending?limit=200", PendingList.serializer()).items

    suspend fun vapidKey(): String = get("/push/vapid-public-key", PushVapidKey.serializer()).publicKey

    suspend fun pushSubscriptions(): List<PushSubscription> =
        get("/me/push-subscriptions", ListSerializer(PushSubscription.serializer()))

    /** Idempotent on the endpoint (the server answers 200 for a known one, 201 for a new one). */
    suspend fun registerPush(body: PushSubscriptionCreate): PushSubscription =
        post("/me/push-subscriptions", PushSubscriptionCreate.serializer(), body, PushSubscription.serializer())

    suspend fun deletePush(id: String) = deleteNoBody("/me/push-subscriptions/$id")

    suspend fun testPush(id: String): PushTestResult = postEmpty("/me/push-subscriptions/$id/test", PushTestResult.serializer())

    suspend fun rawGet(path: String): JsonElement = get(path, JsonElement.serializer())

    // ───────────── gestures ─────────────

    suspend fun stopAgent(org: String, slug: String, reason: String): AgentStopResult =
        post("/orgs/$org/agents/$slug/stop", StopRequest.serializer(), StopRequest(reason), AgentStopResult.serializer())

    suspend fun setWatch(org: String, slug: String, on: Boolean): AgentSocial =
        if (on) put("/orgs/$org/agents/$slug/watch", WatchPut.serializer(), WatchPut(), AgentSocial.serializer())
        else delete("/orgs/$org/agents/$slug/watch", AgentSocial.serializer())

    suspend fun setStar(org: String, slug: String, on: Boolean): AgentSocial =
        if (on) putEmpty("/orgs/$org/agents/$slug/star", AgentSocial.serializer())
        else delete("/orgs/$org/agents/$slug/star", AgentSocial.serializer())

    suspend fun openConversation(org: String, assistant: String): Conversation =
        post("/me/assistants/$org/$assistant/conversations", AssistantConversationOpen.serializer(), AssistantConversationOpen(), Conversation.serializer())

    suspend fun cancelConversation(id: String): ConversationSummary =
        postEmpty("/me/conversations/$id/cancel", ConversationSummary.serializer())

    /** What "stop" means for an entry of the activity feed: a run is cancelled, an agent session closed, a conversation turn cancelled. */
    suspend fun stopActivity(org: String, entry: ActivityEntry, reason: String) {
        when (entry.kind) {
            "run" -> post("/runs/${entry.id}/cancel", StopRequest.serializer(), StopRequest(reason), JsonElement.serializer())
            "session" -> deleteNoBody("/orgs/$org/agent-sessions/${entry.id}")
            else -> cancelConversation(entry.id)
        }
    }

    suspend fun endSession(id: String) = deleteNoBody("/me/sessions/$id")

    /** A decision: the path and body come from [systems.graal.tranibor.pending.Decisions.requestFor]. */
    suspend fun decide(path: String, body: JsonObject?): JsonElement =
        if (body == null) postEmpty(path.removePrefix("/api/v1"), JsonElement.serializer())
        else post(path.removePrefix("/api/v1"), JsonObject.serializer(), body, JsonElement.serializer())

    suspend fun signOut(sessionId: String) = deleteNoBody("/me/sessions/$sessionId")

    // ───────────── streams ─────────────

    /** `POST …/responses` with `stream=true`; [onEvent] sees each server-sent event until the stream ends. */
    suspend fun streamResponse(org: String, slug: String, body: AgentResponseCreate, onEvent: (SseEvent) -> Unit) =
        stream(
            Request.Builder().url(url("/orgs/$org/agents/$slug/responses"))
                .post(ApiJson.encodeToString(AgentResponseCreate.serializer(), body).toRequestBody(jsonType)),
            onEvent,
        )

    /** A message of a durable conversation; its stream names the same events. */
    suspend fun streamMessage(conversationId: String, input: String, onEvent: (SseEvent) -> Unit) =
        stream(
            Request.Builder().url(url("/me/conversations/$conversationId/messages"))
                .post(ApiJson.encodeToString(ConversationMessageCreate.serializer(), ConversationMessageCreate(input)).toRequestBody(jsonType)),
            onEvent,
        )

    /** Every change of the organisation's activity until the caller cancels (or the link drops). */
    suspend fun streamActivity(org: String, onEvent: (SseEvent) -> Unit) =
        stream(Request.Builder().url(url("/orgs/$org/activity/stream")).get(), onEvent, longLived = true)

    private suspend fun stream(builder: Request.Builder, onEvent: (SseEvent) -> Unit, longLived: Boolean = false) {
        val request = authorise(builder).header("Accept", "text/event-stream").build()
        val client = if (longLived) http.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).build() else http
        val response = callWith(client, request)
        response.use {
            if (!it.isSuccessful) throw problem(it)
            val source = it.body?.source() ?: return
            try {
                readSse(source, onEvent)
            } catch (e: IOException) {
                throw NetworkException("The connection dropped.", e)
            }
        }
    }

    // ───────────── plumbing ─────────────

    private fun url(path: String): HttpUrl = (base.toString().trimEnd('/') + path).toHttpUrl()

    private fun authorise(builder: Request.Builder): Request.Builder {
        token()?.let { builder.header("Authorization", "Bearer $it") }
        return builder
    }

    private suspend fun <T> get(path: String, out: KSerializer<T>): T =
        decode(call(authorise(Request.Builder().url(url(path)).get()).build()), out)

    private suspend fun <B, T> post(path: String, inp: KSerializer<B>, body: B, out: KSerializer<T>, auth: Boolean = true): T {
        val builder = Request.Builder().url(url(path)).post(ApiJson.encodeToString(inp, body).toRequestBody(jsonType))
        return decode(call((if (auth) authorise(builder) else builder).build()), out)
    }

    private suspend fun <T> postEmpty(path: String, out: KSerializer<T>): T =
        decode(call(authorise(Request.Builder().url(url(path)).post(EMPTY)).build()), out)

    private suspend fun <B, T> put(path: String, inp: KSerializer<B>, body: B, out: KSerializer<T>): T =
        decode(call(authorise(Request.Builder().url(url(path)).put(ApiJson.encodeToString(inp, body).toRequestBody(jsonType))).build()), out)

    private suspend fun <T> putEmpty(path: String, out: KSerializer<T>): T =
        decode(call(authorise(Request.Builder().url(url(path)).put(EMPTY)).build()), out)

    private suspend fun <T> delete(path: String, out: KSerializer<T>): T =
        decode(call(authorise(Request.Builder().url(url(path)).delete()).build()), out)

    private suspend fun deleteNoBody(path: String) {
        call(authorise(Request.Builder().url(url(path)).delete()).build()).use { if (!it.isSuccessful) throw problem(it) }
    }

    private fun <T> decode(response: Response, out: KSerializer<T>): T = response.use {
        if (!it.isSuccessful) throw problem(it)
        val text = it.body?.string().orEmpty()
        if (text.isBlank()) throw NetworkException("The server answered nothing.")
        try {
            ApiJson.decodeFromString(out, text)
        } catch (e: Exception) {
            throw NetworkException("The server's answer is not what this version of the app expects.", e)
        }
    }

    private suspend fun call(request: Request): Response = callWith(http, request)

    private suspend fun callWith(client: OkHttpClient, request: Request): Response =
        suspendCancellableCoroutine { cont ->
            val call = client.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (cont.isActive) cont.resumeWithException(NetworkException("The instance cannot be reached.", e))
                }

                override fun onResponse(call: Call, response: Response) {
                    if (cont.isActive) cont.resume(response) else response.close()
                }
            })
        }

    companion object {
        private val EMPTY: RequestBody = ByteArray(0).toRequestBody(null)

        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()

        /** An RFC 9457 problem document, read for what the app reacts to. */
        fun problem(response: Response): ApiException {
            val text = runCatching { response.body?.string() }.getOrNull().orEmpty()
            val root = runCatching { ApiJson.parseToJsonElement(text).jsonObject }.getOrNull()
            val codes = linkedSetOf<String>()
            root?.get("code")?.let { (it as? JsonPrimitive)?.contentOrNull?.let(codes::add) }
            (root?.get("errors") as? JsonArray)?.forEach { e ->
                (e as? JsonObject)?.get("error")?.jsonPrimitive?.contentOrNull?.let(codes::add)
            }
            val title = root?.get("title")?.jsonPrimitive?.contentOrNull ?: "HTTP ${response.code}"
            val detail = root?.get("detail")?.jsonPrimitive?.contentOrNull.orEmpty()
            return ApiException(response.code, title, detail, codes)
        }
    }
}
