plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "org.example.cutthefluff"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "org.example.cutthefluff"
        minSdk = 34
        targetSdk = 36
        // BUMPED so Android treats an install as an upgrade rather than a
        // no-op, and so the version on screen says which build is running.
        // A same-versionCode reinstall is legal but gives nothing to check
        // against when someone reports "it still looks the same".
        versionCode = 21
        versionName = "3.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // AppAuth needs the redirect scheme burned into the manifest at
        // build time, via this exact placeholder name -- its own
        // AndroidManifest.xml (merged in from the AAR) declares the
        // RedirectUriReceiverActivity's intent-filter host using it. This
        // is the ONE place that scheme is allowed to live: it must match,
        // character for character, both what AuthConfig.REDIRECT_URI
        // declares in Kotlin and what is registered server-side in
        // rust/auth/src/oidc.rs's OIDC_CLIENTS for the "cutthefluff"
        // client. Three places, one string -- a drift between any two of
        // them is a silent "nothing happens when I tap sign in".
        manifestPlaceholders["appAuthRedirectScheme"] = "cutthefluff"
    }

    buildTypes {
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        // Required for BuildConfig.VERSION_NAME. Off by default in AGP 8+,
        // and the error when it is missing names the symbol, not the flag.
        buildConfig = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.kotlinx.serialization.json)

    // OIDC against the household issuer -- see AuthConfig.kt / AuthManager.kt.
    implementation(libs.appauth)
    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.exifinterface)

    // Chat streaming + RAG API client -- see ChatClient.kt / FindClient.kt.
    implementation(libs.ktor.client.core)
    implementation(libs.ktor.client.okhttp)
    implementation(libs.ktor.client.content.negotiation)
    implementation(libs.ktor.serialization.kotlinx.json)
    implementation(libs.ktor.client.logging)

    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
