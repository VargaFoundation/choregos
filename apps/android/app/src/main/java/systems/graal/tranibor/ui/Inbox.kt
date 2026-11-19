package systems.graal.tranibor.ui

import androidx.compose.foundation.clickable
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import systems.graal.tranibor.Graph
import systems.graal.tranibor.net.ApiException
import systems.graal.tranibor.net.PendingItem
import systems.graal.tranibor.pending.Decisions
import systems.graal.tranibor.pending.Decisions.Verb

/** What waits for the person, and the decisions a phone may take (ADR 0170: a decision needs a recent sign-in of the person's own). */
@Composable
fun InboxScreen(graph: Graph, focus: String?, confirmItYourself: () -> Unit) {
    Load(graph, key = "inbox", fetch = { it.pending() }) { items, reload ->
        if (items.isEmpty()) return@Load Empty("Nothing waits for you.")
        var open by remember { mutableStateOf(focus) }
        LazyColumn(Modifier.fillMaxSize()) {
            items(items, key = { it.id }) { item ->
                Column(Modifier.fillMaxWidth().clickable { open = if (open == item.id) null else item.id }.padding(16.dp)) {
                    Text(Decisions.kindLabel(item.kind), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    Text(Decisions.clean(item.what), style = MaterialTheme.typography.titleSmall)
                    if (open == item.id) Detail(graph, item, confirmItYourself, reload)
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun Detail(graph: Graph, item: PendingItem, confirmItYourself: () -> Unit, done: () -> Unit) {
    Load(graph, key = item.id, fetch = { Decisions.pickDetail(item, it.rawGet(Decisions.detailPath(item))) }) { detail, _ ->
        DetailBody(graph, item, detail, confirmItYourself, done)
    }
}

@Composable
private fun DetailBody(graph: Graph, item: PendingItem, detail: JsonObject?, confirmItYourself: () -> Unit, done: () -> Unit) {
    val scope = rememberCoroutineScope()
    var verb by remember { mutableStateOf<Verb?>(null) }
    var text by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var needFresh by remember { mutableStateOf(false) }

    Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (detail == null) {
            Text("This is not waiting any more.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            return
        }
        Decisions.describe(item, detail).drop(2).forEach { Text(it, style = MaterialTheme.typography.bodyMedium) }
        val verbs = Decisions.verbs(item, detail)
        if (verbs.isEmpty()) Text("This one is completed in the console.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            verbs.forEach { v ->
                val label = when (v) { Verb.Approve -> "Approve"; Verb.Reject -> "Reject"; Verb.Answer -> "Answer" }
                if (v == Verb.Approve) Primary(label) { verb = v } else OutlinedButton(onClick = { verb = v }) { Text(label) }
            }
        }
        if (needFresh) {
            Text("A decision needs a recent sign-in of your own.", color = MaterialTheme.colorScheme.error)
            Primary("Confirm it is you", onClick = confirmItYourself)
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }

    verb?.let { v ->
        val needsText = Decisions.asksReason(v) || v == Verb.Answer
        AlertDialog(
            onDismissRequest = { verb = null },
            title = { Text(when (v) { Verb.Approve -> "Approve?"; Verb.Reject -> "Reject?"; Verb.Answer -> "Your answer" }) },
            text = {
                Column {
                    Text(Decisions.clean(item.what))
                    if (needsText) OutlinedTextField(text, { text = it }, label = { Text(if (v == Verb.Answer) "Answer" else "Reason") })
                }
            },
            confirmButton = {
                TextButton(enabled = !needsText || text.isNotBlank(), onClick = {
                    verb = null
                    scope.launch {
                        val (path, body) = Decisions.requestFor(item, v, reason = text.trim(), answer = text.trim())
                        try {
                            graph.api()?.decide(path, body)
                            text = ""
                            done()
                        } catch (e: ApiException) {
                            when {
                                e.needsFreshSignIn -> needFresh = true
                                e.needsOwnSession -> message = "A decision must be taken from a sign-in of your own, not a token."
                                e.sessionEnded -> graph.signedOut()
                                else -> message = explain(e)
                            }
                        } catch (e: Exception) {
                            message = explain(e)
                        }
                    }
                }) { Text("Confirm") }
            },
            dismissButton = { TextButton(onClick = { verb = null }) { Text("Cancel") } },
        )
    }
}
