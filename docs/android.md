# The Android app

The phone is a surface of the platform ([ADR 0193](adr/0193-the-phone-is-a-surface-of-the-platform.md)): a
native app, in `apps/android`, that manages your agents and assistants from a phone. Like the terminal and
the editor it runs nothing locally: every action is a call to the REST contract of the instance you sign in
to, and what it shows is what the API answers.

Android 10 or later (API 29). No Google services are required.

## Install

The app is not on a store. Take the APK of a release, or the `tranibor-android` artifact of a CI run, and
install it:

```sh
adb install tranibor-0.1.0.apk
```

or open the file on the phone and allow "Install unknown apps" for the app that opened it.

A release APK is signed with the operator's key. An APK built without that key (any CI run, any local
build) is **unsigned**: Android refuses to install it until it is signed, on purpose, so that nothing looks
official that is not. For a local trial, build the debug variant (`./gradlew installDebug`).

## Sign in

1. Open the app and type the address of your instance (`tranibor.example.com` or `https://tranibor.example.com`).
   Only `https` is accepted: a token on a clear-text link is a stolen token.
2. The app shows a code and a button that opens the instance's device page in the browser. Confirm the code
   there, signed in as yourself (the instance's single sign-on applies).
3. The app stores the token it receives encrypted with a key held in the Android Keystore. The session is
   listed under *Account* in the app and under *My account* in the console; ending it in either place ends it.

When the instance asks for a fresh sign-in (to approve something, ADR 0170), the app opens the same flow with
the freshness flag. The app never keeps a way around it.

## What it does

| Tab | What you do there |
|:--|:--|
| Agents | The organisation's agents with their runtime kind; stop an agent, watch it, star it; chat with a prompt agent (text, streamed). |
| Assistants | Your assistants and conversations; continue one, in text, streamed. |
| Activity | What runs now and what just ended, live; stop a run, a session or a turn. |
| Inbox | What waits for you: approve or reject a decision, answer a question, with a reason when you refuse. |
| Agents (voice) | A voice agent has a *Talk* button: a spoken conversation, with a live transcript, mute and hang up. |
| Account | Your sessions on this phone and elsewhere; sign out. |

The organisation shown is the one chosen at the top right; a single organisation is chosen for you.

## Talk to a voice agent

Open a voice agent and press *Talk*; the app asks for the microphone the first time. It speaks to the
agent over the realtime socket of [ADR 0080](adr/0080-hosted-prompt-and-voice-agents.md): 16-bit PCM at
24 kHz, the server detects when you speak, and the app never sends `session.update` (the agent owns the
session's configuration).

- Capture uses the phone's echo cancellation and noise suppression, so the speaker does not feed the agent
  back to itself. Speaking over the agent cuts it off at once (barge-in).
- A Bluetooth headset is used when one is connected, a wired one next, the phone's speaker otherwise.
- The session lives in a foreground service with a notification, so it goes on with the screen off or the
  app in the background; *End session* in the notification hangs up.
- If the link drops, the app resumes the same session within 30 seconds, from the last event it read.
- When the session's spending cap or the agent's time limit is reached, the app says so in words ("The
  spending limit of this voice session is reached.") instead of a close code.

## Notifications

The phone is told when a decision waits for you, a run ends, an agent writes or a session ends, by Web Push
(ADR 0194 (S81-02)) through
[UnifiedPush](https://unifiedpush.org): no Google services.

1. Install a UnifiedPush distributor on the phone (ntfy, from F-Droid or the Play Store).
2. *Account*, *Notifications*, choose the distributor. The app asks the instance for its VAPID key,
   registers with the distributor, and sends the endpoint and its key pair to the instance.
3. *Send a test* checks the chain; a push service that says the endpoint is gone clears the registration.

The message is encrypted for this phone (RFC 8291); the connector library decrypts it with the key it holds
and hands the app the clear payload. A notification opens the place it is about: a decision in the Inbox, a
run or session in Activity, an agent. If the instance has no push configured, the app says so and nothing
is registered.

Approving from the phone follows [ADR 0170](adr/0170-sign-in-security-moves-into-the-core.md): when the
instance asks for a fresh sign-in, the app opens the device flow again with the freshness flag, then retries.

## Build

You need JDK 17 and the Android SDK (`ANDROID_HOME`). From the repository root:

```sh
make android-ci            # Android lint, the unit suite, the unsigned release APK
cd apps/android && ./gradlew testDebugUnitTest
```

The Gradle wrapper is pinned by checksum; the dependencies are in `gradle/libs.versions.toml`. The CI job
`android` runs the same three tasks on every change to the app or to `packages/contracts/openapi.yaml`, and
keeps the APK for seven days.

## Sign a release

The release build signs itself when, and only when, these four variables are set; otherwise it produces an
unsigned APK:

| Variable | Holds |
|:--|:--|
| `ANDROID_KEYSTORE_BASE64` | the keystore file, base64 on one line (`base64 -w0 release.jks`) |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore's password |
| `ANDROID_KEY_ALIAS` | the alias of the key in it |
| `ANDROID_KEY_PASSWORD` | the key's password |

Create the key once and keep it: an update installs over the previous version only if it is signed with the
same key.

```sh
keytool -genkeypair -v -keystore release.jks -alias tranibor -keyalg RSA -keysize 4096 -validity 9125
```

Then build the release on a machine that holds the four variables and attach the APK to the release:

```sh
export ANDROID_KEYSTORE_BASE64=$(base64 -w0 release.jks) ANDROID_KEYSTORE_PASSWORD=... ANDROID_KEY_ALIAS=tranibor ANDROID_KEY_PASSWORD=...
(cd apps/android && ./gradlew assembleRelease -Ptranibor.versionName=1.2.3 -Ptranibor.versionCode=10203)
apksigner verify --print-certs apps/android/app/build/outputs/apk/release/app-release.apk
```

The release workflow does not build the APK yet (it would put an Android build in front of the changelog
job): the APK is built and attached by hand, as above, until the signing secrets are stored as repository
secrets and a release job is added.

## What is not verified

The unit suite covers the client, the sign-in, the stream readers and the parts of the app that hold logic.
The voice session is tested against a fake WebSocket server (state machine, resume, barge-in, caps), the codec and the framing by unit tests. Push registration is tested against a fake distributor and a fake instance (the registration, the test, the clearing of a dead endpoint, the payload parsing and the deep links). A real distributor, a real push delivery and the decryption of a real message, the inbox on a real instance, real audio capture and playback, echo cancellation, Bluetooth routing, the foreground service and the screens have been compiled and linted, not run: no device or emulator took part in this work.
