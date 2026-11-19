package systems.graal.tranibor

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import systems.graal.tranibor.push.PushMessages
import systems.graal.tranibor.ui.TraniborRoot

class MainActivity : ComponentActivity() {
    private val graph get() = (application as TraniborApp).graph

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        route(intent)
        setContent { TraniborRoot(graph) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        route(intent)
    }

    /** A notification, or any `tranibor://` link, names a place; the UI takes it once it is up. */
    private fun route(intent: Intent?) {
        val data = intent?.data ?: return
        PushMessages.destination(data)?.let { graph.link.value = it }
    }
}
