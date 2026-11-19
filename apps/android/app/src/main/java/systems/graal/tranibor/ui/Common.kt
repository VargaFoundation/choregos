package systems.graal.tranibor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import systems.graal.tranibor.Graph
import systems.graal.tranibor.net.ApiClient
import systems.graal.tranibor.net.ApiException
import systems.graal.tranibor.net.NetworkException

/** What a failure says to a person. */
fun explain(e: Throwable): String = when (e) {
    is ApiException -> when {
        e.status == 403 -> "You are not allowed to do this."
        e.status == 404 -> "This does not exist any more."
        else -> e.message ?: "The instance refused the request."
    }
    is NetworkException -> e.message ?: "The instance cannot be reached."
    else -> e.message ?: "Something went wrong."
}

sealed interface Loaded<out T> {
    data object Loading : Loaded<Nothing>
    data class Ok<T>(val value: T) : Loaded<T>
    data class Failed(val error: Throwable) : Loaded<Nothing>
}

/** Fetches once (and again on retry), signing the person out when the session is gone. */
@Composable
fun <T> Load(
    graph: Graph,
    key: Any? = null,
    fetch: suspend (ApiClient) -> T,
    content: @Composable (value: T, reload: () -> Unit) -> Unit,
) {
    var attempt by remember { mutableIntStateOf(0) }
    var state by remember(key) { mutableStateOf<Loaded<T>>(Loaded.Loading) }
    LaunchedEffect(key, attempt) {
        val api = graph.api() ?: return@LaunchedEffect
        try {
            state = Loaded.Ok(fetch(api))
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.sessionEnded) graph.signedOut() else state = Loaded.Failed(e)
        } catch (e: Exception) {
            state = Loaded.Failed(e)
        }
    }
    when (val s = state) {
        Loaded.Loading -> Column(Modifier.fillMaxSize(), Arrangement.Center, Alignment.CenterHorizontally) { CircularProgressIndicator() }
        is Loaded.Failed -> Failure(explain(s.error)) { state = Loaded.Loading; attempt++ }
        is Loaded.Ok -> content(s.value) { attempt++ }
    }
}

@Composable
fun Failure(message: String, retry: (() -> Unit)? = null) {
    Column(Modifier.fillMaxSize().padding(24.dp), Arrangement.Center, Alignment.CenterHorizontally) {
        Text(message, color = MaterialTheme.colorScheme.error)
        if (retry != null) {
            Spacer(Modifier.height(12.dp))
            OutlinedButton(onClick = retry) { Text("Try again") }
        }
    }
}

@Composable
fun Empty(text: String) {
    Column(Modifier.fillMaxSize().padding(24.dp), Arrangement.Center, Alignment.CenterHorizontally) {
        Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun Labelled(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), Arrangement.SpaceBetween) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
fun Primary(text: String, enabled: Boolean = true, onClick: () -> Unit) =
    Button(onClick = onClick, enabled = enabled) { Text(text) }
