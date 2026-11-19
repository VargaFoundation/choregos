package systems.graal.tranibor.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph
import systems.graal.tranibor.net.Agent
import systems.graal.tranibor.net.AgentSocial

@Composable
fun AgentList(graph: Graph, org: String, open: (Agent) -> Unit) {
    Load(graph, key = org, fetch = { it.agents(org) }) { agents, _ ->
        if (agents.isEmpty()) return@Load Empty("No agent in this organisation.")
        LazyColumn(Modifier.fillMaxSize()) {
            items(agents, key = { it.slug }) { agent ->
                Column(Modifier.fillMaxWidth().clickable { open(agent) }.padding(16.dp)) {
                    Text(agent.displayName, style = MaterialTheme.typography.titleMedium)
                    Text(
                        listOfNotNull(agent.status, agent.project, agent.kind).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    agent.description?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyMedium, maxLines = 2) }
                }
                HorizontalDivider()
            }
        }
    }
}

/**
 * One agent: what it is (its runtime kind comes from the version's spec, the list does not carry it), and the
 * gestures a phone does well: talk to it, stop it, follow it.
 */
@Composable
fun AgentDetail(graph: Graph, org: String, agent: Agent, chat: () -> Unit) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var social by remember { mutableStateOf(agent.social ?: AgentSocial()) }
    var message by remember { mutableStateOf<String?>(null) }
    var confirmStop by remember { mutableStateOf(false) }
    var reason by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(agent.displayName, style = MaterialTheme.typography.headlineSmall)
        agent.description?.takeIf { it.isNotBlank() }?.let { Text(it) }
        Labelled("Status", agent.status)
        agent.project?.let { Labelled("Project", it) }
        Labelled("Version", agent.latestVersion.toString())
        Labelled("Watchers", (social.watchers ?: 0).toString())

        Load(graph, key = agent.slug + agent.latestVersion, fetch = { it.agentVersion(org, agent.slug, agent.latestVersion) }) { version, _ ->
            val runtime = version.spec.runtime
            val kind = runtime?.kind ?: "prompt"
            Labelled("Runtime", kind)
            val project = agent.project
            when {
                kind == "voice" -> Text("Talking to a voice agent is not in this build yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                kind == "prompt" && project != null -> Primary("Chat", onClick = chat)
                kind == "hosted" -> Text("A hosted agent runs in the platform's sandbox; its sessions show in Activity.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                else -> Text("This agent cannot be used from the phone.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { graph.api()?.setWatch(org, agent.slug, social.watching == null) }
                        .onSuccess { if (it != null) social = it }.onFailure { message = explain(it) }
                }
            }) { Text(if (social.watching == null) "Watch" else "Unwatch") }
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { graph.api()?.setStar(org, agent.slug, social.starredByMe != true) }
                        .onSuccess { if (it != null) social = it }.onFailure { message = explain(it) }
                }
            }) { Text(if (social.starredByMe == true) "Unstar" else "Star") }
            if (agent.can?.manage == true) {
                OutlinedButton(onClick = { confirmStop = true }) { Text("Stop", color = MaterialTheme.colorScheme.error) }
            }
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }

    if (confirmStop) {
        AlertDialog(
            onDismissRequest = { confirmStop = false },
            title = { Text("Stop ${agent.displayName}?") },
            text = {
                Column {
                    Text("Its runs, sessions and turns in flight are cancelled now.")
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(reason, { reason = it }, label = { Text("Reason") }, singleLine = true)
                }
            },
            confirmButton = {
                TextButton(enabled = reason.isNotBlank(), onClick = {
                    confirmStop = false
                    scope.launch {
                        runCatching { graph.api()?.stopAgent(org, agent.slug, reason.trim()) }
                            .onSuccess { r ->
                                message = r?.let { "Stopped: ${it.runsCancelled} runs, ${it.sessionsClosed} sessions, ${it.turnsCancelled} turns." }
                            }.onFailure { message = explain(it) }
                        reason = ""
                    }
                }) { Text("Stop it") }
            },
            dismissButton = { TextButton(onClick = { confirmStop = false }) { Text("Cancel") } },
        )
    }
}
