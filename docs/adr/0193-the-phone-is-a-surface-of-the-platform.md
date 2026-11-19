# 0193 — The phone is a surface of the platform

- **Status**: accepted, 2026-11-19 (the project lead's request of the same day; written by story S81-01)
- **Concerns**: a new `apps/android` (stories S81-04 to S81-07), a person's devices told by Web Push
  (ADR 0194, stories S81-02 and S81-03); builds on
  [ADR 0080](0080-hosted-prompt-and-voice-agents.md) (voice sessions),
  [ADR 0154](0154-the-terminal-is-a-surface-of-the-platform.md) and
  [ADR 0155](0155-the-editor-is-a-surface-of-the-platform.md) (a surface runs nothing locally),
  [ADR 0168](0168-the-surfaces-frozen-for-1-0.md) (the REST contract it calls) and
  [ADR 0170](0170-sign-in-security-moves-into-the-core.md) (re-authentication to approve)

## Context

A person who runs agents and assistants on the platform has three surfaces today: the console, the
terminal (ADR 0154) and the editor (ADR 0155). None of them is at hand away from a desk, and none can
hold a spoken conversation: the voice agents of ADR 0080 are served (`/orgs/{org}/agents/{slug}/realtime`)
but no surface of the platform speaks to them.

The console is usable on a phone's screen, but a browser tab is a poor host for a voice session: it is
suspended when the screen turns off, it does not keep a Bluetooth headset's route, and it cannot be told
that a decision waits while it is closed.

## Decision

1. **A native Android app is a fourth surface, in this repository** (`apps/android`, Kotlin and Jetpack
   Compose). Like the terminal and the editor, it runs nothing locally: every action is a call to the
   REST contract, the voice session is the socket ADR 0080 serves, and what it shows is what the API
   answers.
2. **It signs in as the person, through the device-code flow the terminal uses** (`/auth/device`).
   The session it holds is listed under My account and ended there like any other; the token is kept in
   storage backed by the Android Keystore.
3. **It speaks to voice agents over the same socket and under the same rules as any client**: the agent
   owns the session's configuration (no `session.update`), the session's spend cap and duration close it,
   a transcript is kept by the platform and audio never is. The app captures with the platform's echo
   cancellation and noise suppression, keeps the session in a foreground service while the screen is
   off, and follows a Bluetooth headset.
4. **What waits for the person reaches the phone by Web Push** (RFC 8030, payload encrypted per
   RFC 8291, VAPID per RFC 8292), through UnifiedPush on the device. The server speaks the standard only
   and names no push vendor; a payload carries a kind, a title, one line and references, never content
   the person would need the platform's rights to read. ADR 0194 records the server side.
5. **Approving from the phone obeys ADR 0170**: a decision that requires a fresh sign-in asks for one on
   the phone; the app never stores a way around it.
6. **The app is built and tested by its own CI job** and released beside the CLI's binaries (ADR 0151),
   signed with a key the operator holds.

## Consequences

- A fourth surface to keep working when the REST contract moves; the frozen surfaces of ADR 0168 are
  what limits that cost.
- A new server surface: a person's push subscriptions (ADR 0194).
- iOS is not covered.

## Rejected alternatives

- **A progressive web app.** No foreground service for a long voice session, no reliable audio route,
  and Web Push in a mobile browser depends on the browser's vendor.
- **An app in another repository, on the frozen surfaces only.** Considered while ADR 0180's freeze held;
  [ADR 0192](0192-the-scope-freeze-is-lifted.md) lifted it.
- **A cross-platform framework (React Native, Flutter).** The hard part is audio and the foreground
  service, which are native in both cases; one platform is asked for.
