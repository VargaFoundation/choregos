package systems.graal.tranibor

import android.app.Application
import systems.graal.tranibor.push.Notifier

class TraniborApp : Application() {
    lateinit var graph: Graph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = Graph(this)
        Notifier.ensureChannel(this)
    }
}
