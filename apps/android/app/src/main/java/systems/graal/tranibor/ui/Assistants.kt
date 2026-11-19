package systems.graal.tranibor.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph

/** The assistants the person may use, across organisations, and their durable conversations. */
@Composable
fun AssistantsScreen(graph: Graph, openConversation: (id: String, title: String) -> Unit) {
    val scope = rememberCoroutineScope()
    Load(graph, key = "assistants", fetch = { it.assistants() to it.conversations() }) { (assistants, conversations), _ ->
        LazyColumn(Modifier.fillMaxSize()) {
            if (assistants.isEmpty() && conversations.isEmpty()) item { Empty("No assistant is open to you.") }
            if (assistants.isNotEmpty()) item { Text("Assistants", Modifier.padding(16.dp, 12.dp), style = MaterialTheme.typography.titleSmall) }
            items(assistants, key = { it.org + "/" + it.slug }) { assistant ->
                Column(Modifier.fillMaxWidth().clickable {
                    scope.launch {
                        runCatching { graph.api()?.openConversation(assistant.org, assistant.slug) }
                            .onSuccess { c -> if (c != null) openConversation(c.id, assistant.displayName ?: assistant.name) }
                    }
                }.padding(16.dp)) {
                    Text(assistant.displayName ?: assistant.name, style = MaterialTheme.typography.titleMedium)
                    Text("${assistant.org} · ${assistant.status}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    assistant.description?.takeIf { it.isNotBlank() }?.let { Text(it, maxLines = 2) }
                }
                HorizontalDivider()
            }
            if (conversations.isNotEmpty()) item { Text("Conversations", Modifier.padding(16.dp, 12.dp), style = MaterialTheme.typography.titleSmall) }
            items(conversations, key = { it.id }) { conversation ->
                Column(Modifier.fillMaxWidth().clickable { openConversation(conversation.id, conversation.title ?: conversation.agent) }.padding(16.dp)) {
                    Text(conversation.title ?: conversation.agent, style = MaterialTheme.typography.titleMedium)
                    Text("${conversation.status} · ${conversation.turns} turns", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                HorizontalDivider()
            }
        }
    }
}
