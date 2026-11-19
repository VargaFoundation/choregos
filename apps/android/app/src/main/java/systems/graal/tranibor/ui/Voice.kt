package systems.graal.tranibor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import systems.graal.tranibor.Graph
import systems.graal.tranibor.voice.Phase
import systems.graal.tranibor.voice.Role

/** A live voice session: what is heard, what is said, and the gestures (mute, interrupt, hang up). */
@Composable
fun VoiceScreen(graph: Graph, leave: () -> Unit) {
    val voice = graph.voice
    val state by voice.state.collectAsState()
    val s = state
    if (s == null) {
        LaunchedEffect(Unit) { leave() }
        return
    }
    val listState = rememberLazyListState()
    LaunchedEffect(s.transcript.size, s.transcript.lastOrNull()?.text?.length) {
        if (s.transcript.isNotEmpty()) listState.scrollToItem(s.transcript.lastIndex)
    }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Text(voice.config?.title.orEmpty(), style = MaterialTheme.typography.titleLarge)
        Text(
            when (s.phase) {
                Phase.Connecting -> "Connecting..."
                Phase.Listening -> if (s.muted) "Muted" else "Listening"
                Phase.Speaking -> "Speaking"
                Phase.Reconnecting -> "Connection lost, reconnecting..."
                Phase.Ended -> "Ended"
            },
            color = MaterialTheme.colorScheme.primary,
        )
        if (s.phase != Phase.Ended) {
            Spacer(Modifier.height(8.dp))
            LinearProgressIndicator(progress = { if (s.muted) 0f else s.micLevel.coerceIn(0f, 1f) }, Modifier.fillMaxWidth())
        }
        Spacer(Modifier.height(8.dp))
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(s.transcript, key = { it.id }) { line ->
                Box(Modifier.fillMaxWidth(), contentAlignment = if (line.role == Role.Person) Alignment.CenterEnd else Alignment.CenterStart) {
                    Surface(
                        shape = MaterialTheme.shapes.medium,
                        color = if (line.role == Role.Person) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                    ) {
                        Text(line.text, Modifier.padding(10.dp), color = if (line.final) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        s.notice?.let { n ->
            Column(Modifier.padding(vertical = 8.dp)) {
                Text(n.title, style = MaterialTheme.typography.titleSmall)
                n.detail?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (s.phase == Phase.Ended) {
                Primary("Close") { voice.dismiss(); leave() }
            } else {
                OutlinedButton(onClick = { voice.mute(!s.muted) }) { Text(if (s.muted) "Unmute" else "Mute") }
                OutlinedButton(enabled = s.phase == Phase.Speaking, onClick = { voice.interrupt() }) { Text("Interrupt") }
                OutlinedButton(onClick = { voice.stop() }) { Text("Hang up", color = MaterialTheme.colorScheme.error) }
            }
        }
    }
}
