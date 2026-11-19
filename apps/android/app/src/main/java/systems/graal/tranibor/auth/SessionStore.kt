package systems.graal.tranibor.auth

import android.content.Context
import android.util.Base64
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** What the app keeps of a sign-in: the instance, the bearer, the id `GET /me/sessions` lists. */
@Serializable
data class Session(val origin: String, val token: String, val sessionId: String, val authTime: String? = null)

/** Where the sealed session lives. The device's backup never carries it (see `data_extraction_rules.xml`). */
interface BlobStore {
    fun read(): ByteArray?
    fun write(blob: ByteArray?)
}

class PrefsBlobStore(context: Context, private val name: String = "session") : BlobStore {
    private val prefs = context.getSharedPreferences("tranibor.secure", Context.MODE_PRIVATE)

    override fun read(): ByteArray? = prefs.getString(name, null)?.let { Base64.decode(it, Base64.NO_WRAP) }

    override fun write(blob: ByteArray?) {
        prefs.edit().apply { if (blob == null) remove(name) else putString(name, Base64.encodeToString(blob, Base64.NO_WRAP)) }.apply()
    }
}

/** The signed-in session, encrypted at rest. A blob that does not open is treated as no session at all. */
class SessionStore(private val box: SecretBox, private val blobs: BlobStore) {
    private val json = Json { ignoreUnknownKeys = true }

    fun load(): Session? {
        val blob = blobs.read() ?: return null
        val plain = box.open(blob)
        if (plain == null) {
            blobs.write(null)
            return null
        }
        return runCatching { json.decodeFromString(Session.serializer(), plain.decodeToString()) }.getOrNull()
    }

    fun save(session: Session) {
        blobs.write(box.seal(json.encodeToString(Session.serializer(), session).encodeToByteArray()))
    }

    fun clear() = blobs.write(null)
}
