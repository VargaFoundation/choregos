package systems.graal.tranibor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph
import systems.graal.tranibor.activity.ActivityFeed
import systems.graal.tranibor.net.ActivityEntry
import systems.graal.tranibor.net.ApiException

/** What runs in the organisation now, and what ended in the last hour; it follows the stream while the screen is open. */
@Composable
fun ActivityScreen(graph: Graph, org: String) {
    Load(graph, key = org, fetch = { it.activity(org) }) { first, _ ->
        var feed by remember { mutableStateOf(ActivityFeed.of(first)) }
        var message by remember { mutableStateOf<String?>(null) }
        var stopping by remember { mutableStateOf<ActivityEntry?>(null) }
        var reason by remember { mutableStateOf("") }
        val scope = rememberCoroutineScope()

        // The stream lives with the screen; a dropped link is retried, a refusal ends the attempts.
        LaunchedEffect(org) {
            var wait = 1_000L
            while (true) {
                try {
                    graph.api()?.streamActivity(org) { feed = ActivityFeed.apply(feed, it) ?: feed; wait = 1_000L }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiException) {
                    if (e.sessionEnded) graph.signedOut()
                    return@LaunchedEffect
                } catch (_: Exception) {
                }
                delay(wait)
                wait = (wait * 2).coerceAtMost(15_000)
            }
        }

        if (feed.inFlight.isEmpty() && feed.finished.isEmpty()) return@Load Empty("Nothing is running, and nothing ended in the last hour.")
        LazyColumn(Modifier.fillMaxSize()) {
            message?.let { item { Text(it, Modifier.padding(16.dp), color = MaterialTheme.colorScheme.error) } }
            if (feed.inFlight.isNotEmpty()) item { Text("Running", Modifier.padding(16.dp, 12.dp), style = MaterialTheme.typography.titleSmall) }
            items(feed.inFlight, key = { "i" + it.id }) { Entry(it) { stopping = it } }
            if (feed.finished.isNotEmpty()) item { Text("Ended recently", Modifier.padding(16.dp, 12.dp), style = MaterialTheme.typography.titleSmall) }
            items(feed.finished, key = { "f" + it.id }) { Entry(it, null) }
        }

        stopping?.let { entry ->
            AlertDialog(
                onDismissRequest = { stopping = null },
                title = { Text("Stop this ${entry.kind}?") },
                text = { OutlinedTextField(reason, { reason = it }, label = { Text("Reason") }, singleLine = true) },
                confirmButton = {
                    TextButton(enabled = reason.isNotBlank(), onClick = {
                        stopping = null
                        scope.launch {
                            runCatching { graph.api()?.stopActivity(org, entry, reason.trim()) }
                                .onSuccess { message = null }.onFailure { message = explain(it) }
                            reason = ""
                        }
                    }) { Text("Stop it") }
                },
                dismissButton = { TextButton(onClick = { stopping = null }) { Text("Cancel") } },
            )
        }
    }
}

@Composable
private fun Entry(entry: ActivityEntry, stop: (() -> Unit)?) {
    Column(Modifier.fillMaxWidth().padding(16.dp, 8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(ActivityFeed.title(entry), style = MaterialTheme.typography.titleSmall)
                Text(
                    listOfNotNull(entry.kind, entry.status, entry.projectSlug, entry.model, entry.spend?.let { "$%.2f".format(it.costUsd) }).joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                entry.failure?.summary?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            }
            if (stop != null && entry.canStop) OutlinedButton(onClick = stop) { Text("Stop") }
        }
    }
    HorizontalDivider()
}
