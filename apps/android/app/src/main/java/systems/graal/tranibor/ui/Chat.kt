package systems.graal.tranibor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph
import systems.graal.tranibor.chat.ChatEvent
import systems.graal.tranibor.chat.ChatEvents
import systems.graal.tranibor.net.AgentResponseCreate
import systems.graal.tranibor.net.ApiException
import systems.graal.tranibor.net.SseEvent

data class Bubble(val mine: Boolean, val text: String)

/**
 * A text conversation: the person's messages and the agent's answer, which grows as the stream delivers it.
 * [send] runs one turn and feeds every event to its callback; leaving the screen cancels the turn.
 */
@Composable
fun ChatScreen(
    graph: Graph,
    initial: List<Bubble>,
    send: suspend (input: String, onEvent: (SseEvent) -> Unit) -> Unit,
    onPending: (Int) -> Unit = {},
    onCancelTurn: (suspend () -> Unit)? = null,
) {
    val bubbles = remember { mutableStateListOf<Bubble>().also { it.addAll(initial) } }
    var draft by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    LaunchedEffect(bubbles.size, bubbles.lastOrNull()?.text?.length) {
        if (bubbles.isNotEmpty()) listState.scrollToItem(bubbles.lastIndex)
    }

    fun submit() {
        val text = draft.trim()
        if (text.isEmpty() || busy) return
        draft = ""
        error = null
        busy = true
        bubbles += Bubble(true, text)
        bubbles += Bubble(false, "")
        job = scope.launch {
            try {
                send(text) { raw ->
                    when (val e = ChatEvents.parse(raw)) {
                        is ChatEvent.Delta -> bubbles[bubbles.lastIndex] = bubbles.last().copy(text = bubbles.last().text + e.text)
                        is ChatEvent.Failed -> error = e.message
                        is ChatEvent.Done -> onPending(e.pending.size)
                        else -> Unit
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                if (e.sessionEnded) graph.signedOut() else error = explain(e)
            } catch (e: Exception) {
                error = explain(e)
            } finally {
                if (bubbles.lastOrNull()?.let { !it.mine && it.text.isEmpty() } == true) bubbles.removeAt(bubbles.lastIndex)
                busy = false
            }
        }
    }

    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp), state = listState, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(bubbles.size) { i ->
                val b = bubbles[i]
                Box(Modifier.fillMaxWidth(), contentAlignment = if (b.mine) Alignment.CenterEnd else Alignment.CenterStart) {
                    Surface(
                        shape = MaterialTheme.shapes.medium,
                        color = if (b.mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                    ) { Text(b.text.ifEmpty { "..." }, Modifier.padding(10.dp)) }
                }
            }
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 12.dp)) }
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(draft, { draft = it }, Modifier.weight(1f), placeholder = { Text("Message") }, maxLines = 4, enabled = !busy)
            if (busy) {
                OutlinedButton(onClick = {
                    job?.cancel()
                    onCancelTurn?.let { cancel -> scope.launch { runCatching { cancel() } } }
                }) { Text("Stop") }
            } else {
                Primary("Send", enabled = draft.isNotBlank(), onClick = ::submit)
            }
        }
    }
}

/** A text conversation with a prompt agent of a project (`POST /orgs/{org}/agents/{slug}/responses`). */
@Composable
fun AgentChat(graph: Graph, org: String, slug: String, project: String) {
    var sessionId by remember { mutableStateOf<String?>(null) }
    ChatScreen(
        graph = graph,
        initial = emptyList(),
        send = { input, onEvent ->
            val api = graph.api() ?: return@ChatScreen
            api.streamResponse(org, slug, AgentResponseCreate(project, input, sessionId)) { e ->
                (ChatEvents.parse(e) as? ChatEvent.Started)?.let { sessionId = it.sessionId.ifEmpty { null } }
                onEvent(e)
            }
        },
    )
}

/** A durable conversation with an assistant. */
@Composable
fun ConversationChat(graph: Graph, id: String) {
    Load(graph, key = id, fetch = { it.conversation(id) }) { conversation, _ ->
        ChatScreen(
            graph = graph,
            initial = conversation.messages.filter { it.role != "system" && it.role != "tool" && it.content.isNotBlank() }.map { Bubble(it.role == "user", it.content) },
            send = { input, onEvent -> graph.api()?.streamMessage(id, input, onEvent) },
            // Stopping a turn cancels it on the platform, which books what it spent (the CLI does the same on Ctrl-C).
            onCancelTurn = { graph.api()?.cancelConversation(id) },
        )
    }
}
