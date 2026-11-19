package systems.graal.tranibor.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import systems.graal.tranibor.Graph
import systems.graal.tranibor.auth.DeviceSignIn
import systems.graal.tranibor.auth.Session
import systems.graal.tranibor.net.DeviceAuthorization
import systems.graal.tranibor.net.Instance

private sealed interface Step {
    data object Typing : Step
    data object Asking : Step
    data class Waiting(val codes: DeviceAuthorization, val flow: DeviceSignIn) : Step
}

/** Sign in against a typed instance with the device flow (RFC 8628): the approval happens in the browser, signed in as the person. */
@Composable
fun SignInScreen(graph: Graph, fresh: Boolean = false, onDone: () -> Unit = {}) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var typed by remember { mutableStateOf(graph.session.value?.origin ?: graph.prefs.get("origin").orEmpty()) }
    var step by remember { mutableStateOf<Step>(Step.Typing) }
    var error by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }

    fun begin() {
        val origin = try {
            Instance.normalise(typed)
        } catch (e: Instance.Invalid) {
            error = e.message
            return
        }
        error = null
        step = Step.Asking
        job = scope.launch {
            try {
                val flow = graph.signInFor(origin)
                val codes = flow.start()
                step = Step.Waiting(codes, flow)
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(flow.link(codes, fresh))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                val token = flow.await(codes)
                graph.prefs.put("origin", origin)
                graph.signedIn(Session(origin, token.accessToken, token.sessionId, token.authTime))
                onDone()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = explain(e)
                step = Step.Typing
            }
        }
    }

    Column(Modifier.fillMaxSize().padding(24.dp), Arrangement.Center) {
        Text("Tranibor", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(24.dp))
        when (val s = step) {
            Step.Typing, Step.Asking -> {
                OutlinedTextField(
                    value = typed,
                    onValueChange = { typed = it },
                    label = { Text("Instance") },
                    singleLine = true,
                    enabled = s == Step.Typing,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                Primary(if (fresh) "Confirm it is you" else "Sign in", enabled = s == Step.Typing && typed.isNotBlank(), onClick = ::begin)
                if (s == Step.Asking) CircularProgressIndicator(Modifier.padding(top = 16.dp))
            }
            is Step.Waiting -> {
                Text("Approve this device in the browser that just opened. The code it shows must be:")
                Spacer(Modifier.height(12.dp))
                Text(s.codes.userCode, style = MaterialTheme.typography.displaySmall, color = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.height(12.dp))
                OutlinedButton(onClick = {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(s.flow.link(s.codes, fresh))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }) { Text("Open the browser again") }
                Spacer(Modifier.height(8.dp))
                OutlinedButton(onClick = { job?.cancel(); step = Step.Typing }) { Text("Cancel") }
                CircularProgressIndicator(Modifier.padding(top = 16.dp))
            }
        }
        error?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = MaterialTheme.colorScheme.error)
        }
    }
}
