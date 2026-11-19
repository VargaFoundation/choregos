package systems.graal.tranibor.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import systems.graal.tranibor.Graph
import systems.graal.tranibor.net.Agent

private enum class Tab(val label: String) { Agents("Agents"), Assistants("Assistants"), Activity("Activity"), Account("Account") }

private sealed interface Route {
    data class AgentDetail(val slug: String) : Route
    data class AgentChat(val agent: Agent) : Route
    data class Chat(val id: String, val title: String) : Route
    data object Voice : Route
}

@Composable
fun TraniborRoot(graph: Graph) {
    TraniborTheme {
        val session by graph.session.collectAsState()
        if (session == null) SignInScreen(graph) else Signed(graph)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Signed(graph: Graph) {
    val stack = remember { mutableStateListOf<Route>() }
    var tab by remember { mutableStateOf(Tab.Agents) }
    var org by remember { mutableStateOf(graph.prefs.get("org")) }
    val voice by graph.voice.state.collectAsState()

    val route = stack.lastOrNull()
    BackHandler(enabled = route != null) { stack.removeLastOrNull() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(titleOf(route, tab)) },
                navigationIcon = {
                    if (route != null) IconButton(onClick = { stack.removeLastOrNull() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                },
                actions = { if (route == null && tab != Tab.Assistants && tab != Tab.Account) OrgPicker(graph, org) { org = it; graph.prefs.put("org", it) } },
            )
        },
        bottomBar = {
            if (route == null) NavigationBar {
                Tab.entries.forEach { t ->
                    NavigationBarItem(selected = tab == t, onClick = { tab = t }, icon = {}, label = { Text(t.label, maxLines = 1) }, alwaysShowLabel = true)
                }
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            if (route == null) {
                Column2(voiceBanner = voice != null && voice?.phase != systems.graal.tranibor.voice.Phase.Ended, open = { stack += Route.Voice })
                OrgScoped(graph, org, tab, openAgent = { stack += Route.AgentDetail(it.slug) }, openChat = { id, title -> stack += Route.Chat(id, title) })
            } else when (route) {
                is Route.AgentDetail -> {
                    val o = org
                    if (o != null) Load(graph, key = o to route.slug, fetch = { api -> api.agents(o).first { it.slug == route.slug } }) { agent, _ ->
                        AgentDetail(graph, o, agent, chat = { stack += Route.AgentChat(agent) }, talk = { stack += Route.Voice })
                    }
                }
                is Route.AgentChat -> org?.let { AgentChat(graph, it, route.agent.slug, route.agent.project.orEmpty()) }
                is Route.Chat -> ConversationChat(graph, route.id)
                Route.Voice -> VoiceScreen(graph, leave = { stack.removeLastOrNull() })
            }
        }
    }
}

@Composable
private fun Column2(voiceBanner: Boolean, open: () -> Unit) {
    if (!voiceBanner) return
    Row(
        Modifier.fillMaxWidth().clickable(onClick = open).padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("A voice session is in progress.", color = MaterialTheme.colorScheme.primary)
        TextButton(onClick = open) { Text("Return") }
    }
}

@Composable
private fun OrgScoped(
    graph: Graph, org: String?, tab: Tab,
    openAgent: (Agent) -> Unit, openChat: (String, String) -> Unit,
) {
    when (tab) {
        Tab.Assistants -> AssistantsScreen(graph, openChat)
        Tab.Account -> AccountScreen(graph)
        Tab.Agents -> if (org == null) PickOrg(graph) else AgentList(graph, org, openAgent)
        Tab.Activity -> if (org == null) PickOrg(graph) else ActivityScreen(graph, org)
    }
}

@Composable
private fun PickOrg(graph: Graph) {
    Empty("Choose an organisation with the menu at the top right.")
}

@Composable
private fun OrgPicker(graph: Graph, current: String?, pick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Load(graph, key = "orgs", fetch = { it.orgs() }) { orgs, _ ->
        // A single organisation is chosen for the person, once.
        LaunchedEffect(orgs) { if (current == null && orgs.size == 1) pick(orgs.first().slug) }
        TextButton(onClick = { open = true }) { Text(current ?: "Organisation") }
        DropdownMenu(open, { open = false }) {
            orgs.forEach { o -> DropdownMenuItem(text = { Text(o.name.ifBlank { o.slug }) }, onClick = { open = false; pick(o.slug) }) }
        }
    }
}

private fun titleOf(route: Route?, tab: Tab): String = when (route) {
    null -> tab.label
    is Route.AgentDetail -> "Agent"
    is Route.AgentChat -> route.agent.displayName
    is Route.Chat -> route.title
    Route.Voice -> "Voice"
}
