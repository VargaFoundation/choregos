package systems.graal.tranibor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import systems.graal.tranibor.ui.TraniborRoot

class MainActivity : ComponentActivity() {
    private val graph get() = (application as TraniborApp).graph

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { TraniborRoot(graph) }
    }
}
