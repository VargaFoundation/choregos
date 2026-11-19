package systems.graal.tranibor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph

/** The person's sessions (this phone's among them), the notification setting, and sign-out. */
@Composable
fun AccountScreen(graph: Graph) {
    val scope = rememberCoroutineScope()
    val session by graph.session.collectAsState()
    Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Labelled("Instance", session?.origin.orEmpty())
        Spacer(Modifier.height(8.dp))
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        Text("Sessions", style = MaterialTheme.typography.titleMedium)
        Load(graph, key = "sessions", fetch = { it.sessions() }) { sessions, reload ->
            Column {
                sessions.forEach { s ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text((s.clientName ?: s.kind) + if (s.current) " (this phone)" else "", style = MaterialTheme.typography.bodyLarge)
                            Text(
                                listOfNotNull(s.kind, s.ip, s.lastSeenAt?.let { "seen $it" }).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (!s.current) OutlinedButton(onClick = { scope.launch { runCatching { graph.api()?.endSession(s.id) }; reload() } }) { Text("End") }
                    }
                }
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp))
        OutlinedButton(onClick = {
            scope.launch {
                // End the session on the server first: a token left alive after the phone forgets it is a token nobody holds.
                session?.let { s -> runCatching { graph.api()?.signOut(s.sessionId) } }
                graph.signedOut()
            }
        }) { Text("Sign out of this phone") }
    }
}
