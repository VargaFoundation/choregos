import java.util.Base64

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Release signing comes from the environment (a CI secret), never from a file of the repository. Without
// `ANDROID_KEYSTORE_BASE64` the release build is unsigned: a debug-signed release would be installable and
// look official, which is worse than an APK that refuses to install until it is signed.
val keystoreBase64: String? = System.getenv("ANDROID_KEYSTORE_BASE64")?.takeIf { it.isNotBlank() }
val signingKeystore: File? = keystoreBase64?.let {
    layout.buildDirectory.file("signing/release.jks").get().asFile.also { file ->
        file.parentFile.mkdirs()
        file.writeBytes(Base64.getMimeDecoder().decode(it))
    }
}

android {
    namespace = "systems.graal.tranibor"
    compileSdk = 37

    defaultConfig {
        applicationId = "systems.graal.tranibor"
        minSdk = 29
        targetSdk = 36
        versionCode = (project.findProperty("tranibor.versionCode") as String?)?.toInt() ?: 1
        versionName = (project.findProperty("tranibor.versionName") as String?) ?: "0.1.0"
        // The scheme of the deep links a notification opens (`tranibor://decision/<id>`).
        manifestPlaceholders["deepLinkScheme"] = "tranibor"
    }

    signingConfigs {
        if (signingKeystore != null) {
            create("release") {
                storeFile = signingKeystore
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            // R8 is left off until the app has been exercised on a device: a shrinker rule missed in
            // `kotlinx.serialization` or OkHttp only shows at runtime, and no device ran this build.
            isMinifyEnabled = false
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }

    lint {
        abortOnError = true
        warningsAsErrors = false
        checkReleaseBuilds = true
    }

    testOptions {
        unitTests.isIncludeAndroidResources = true
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.okhttp)
    implementation(libs.unifiedpush.connector)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.robolectric)
    testImplementation(libs.kotlinx.serialization.json)
}
