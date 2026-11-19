package systems.graal.tranibor.auth

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Seals and opens a small secret. What is sealed is useless without the key, which never leaves its holder. */
interface SecretBox {
    fun seal(plain: ByteArray): ByteArray

    /** The secret, or `null` when [sealed] is not ours (a key lost with a restore or a wipe, a tampered blob). */
    fun open(sealed: ByteArray): ByteArray?
}

/** AES-256-GCM. The blob is `iv (12 bytes) || ciphertext+tag`; a fresh random IV is drawn for each seal. */
class AesGcmBox(private val key: () -> SecretKey) : SecretBox {
    private val random = SecureRandom()

    override fun seal(plain: ByteArray): ByteArray {
        val iv = ByteArray(IV_BYTES).also(random::nextBytes)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, iv))
        return iv + cipher.doFinal(plain)
    }

    override fun open(sealed: ByteArray): ByteArray? {
        if (sealed.size <= IV_BYTES) return null
        return try {
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed.copyOfRange(0, IV_BYTES)))
            cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        const val IV_BYTES = 12
        const val TAG_BITS = 128
        private const val TRANSFORM = "AES/GCM/NoPadding"

        /** A key held by the Android Keystore: generated once, non-exportable, used inside the secure hardware when there is one. */
        fun keystore(alias: String = "tranibor.session"): AesGcmBox = AesGcmBox {
            val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            (store.getKey(alias, null) as? SecretKey) ?: KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
                .apply {
                    init(
                        KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                            .setKeySize(256)
                            .build(),
                    )
                }.generateKey()
        }

        /** For tests only: a key held in memory. */
        fun inMemory(bytes: ByteArray = ByteArray(32) { it.toByte() }): AesGcmBox {
            val key = SecretKeySpec(bytes, "AES")
            return AesGcmBox { key }
        }
    }
}
