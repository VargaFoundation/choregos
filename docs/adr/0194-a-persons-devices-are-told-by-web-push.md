# 0194 — A person's devices are told by Web Push

- **Status**: accepted, 2026-11-19 (asked by the project lead; stories S81-02 and S81-03)
- **Concerns**: `packages/contracts/openapi.yaml` (`/push/vapid-public-key`, `/me/push-subscriptions*`,
  `PushPayload`), `apps/api/src/tranibor_api/push/`, `routers/push.py`, the table `push_subscriptions`,
  `services/agent_social.py` (`notify`), `services/actions.py` (`propose`),
  `charts/choregos` (the VAPID secret); builds on ADR 0193 (the phone is a surface
  of the platform), keeps [ADR 0005](0005-adapters-and-fakes.md) (fakes),
  [ADR 0008](0008-idempotence-and-run-id.md) (idempotence) and [ADR 0026](0026-what-the-core-rls-covers.md)
  (row-level security)

## Context

The phone application (stream S81) shows what waits for a person and what happened: a decision to take,
a run that ended, an item their watched agents raised. A phone that must be opened to learn it does not
serve that person. Pushing to a phone usually means a vendor's cloud (FCM, APNs) and an SDK in the
application; the platform must also run where neither is acceptable (a sovereign installation, a
phone without Google services).

Web Push is the open standard that fits: RFC 8030 (the protocol), RFC 8291 (the encrypted payload,
`aes128gcm`) and RFC 8292 (VAPID, the application server's identity). The phone side is UnifiedPush,
whose distributors (ntfy, by default) hand the application a Web Push endpoint and its keys; a
browser's `PushManager` yields the same three values. The server needs no vendor.

## Decision

1. **A device is a Web Push subscription of a person.** `POST /me/push-subscriptions` registers
   `{endpoint, keys: {p256dh, auth}, device_name, kinds?}`, idempotent on `endpoint`; `GET` lists the
   person's devices (never the endpoint, which is a capability: its host is shown); `DELETE .../{id}`
   forgets one; `POST .../{id}/test` sends a `push.test` message and says what the push service
   answered. `GET /push/vapid-public-key` gives the key a device subscribes with. A person only: a
   service account or an outpost has no phone.
2. **The payload is minimal and documented.** `PushPayload`: `{kind, title, body, org, ref: {type, id},
   url, at}`, nothing else. A title, one line, references the application resolves with its own
   credential, and a console-relative path. No secret, no work-item content, no agent output. The push
   service sees ciphertext only (RFC 8291), but the payload is minimal anyway: a lost phone's lock
   screen is the real reader.
3. **Four kinds, each sent where the platform already records the fact.** `agent.notification` where
   an agent notification is written (`agent_social.notify`, one per watcher); `decision.awaiting` where
   an action is proposed and waits for a person (`actions.propose`), sent to the people the action's
   rule lets decide; `run.ended` and `session.ended` where a run or a hosted session the person started
   reaches a final state. A device may ask for a subset (`kinds`).
4. **Only people who hold the right are told.** The recipient list is computed with the same rule that
   lets a person read or decide the thing: an approver's rank in the project (and no one told of their
   own proposal), a watcher of an agent they may still see, the person who started the run. A rule that
   names an identity-provider group is not evaluated for a person who is not signed in (their groups are
   those of their last sign-in): such a rule sends no push, and the decision stays in the console's inbox.
   A push never widens who knows.
5. **Delivery never blocks nor fails the originating transaction.** It is registered after commit
   (`db.session.after_commit`) and runs as a background task in its own platform-scoped session, with a
   timeout per push service and a bounded concurrency. A crash between commit and delivery loses the
   push: it is a notification, the inbox and the console remain the record, and the platform says so
   rather than promising an outbox it does not run. A replay finds no effect to double: the message
   carries `Topic`-less, `TTL`-bounded delivery, and a device sees at worst the same line twice.
6. **A gone device is forgotten.** A 404 or 410 from the push service deletes the subscription. Other
   failures (5xx, timeout, 429) are counted on the row (`last_failure_at`) and never retried by the
   platform; a row that has failed continuously for 30 days is deleted by the next failure.
7. **The endpoint is a URL the client chose, so the server is careful with it.** https only, a public
   host (the name is resolved, a private, loopback or link-local address refuses the registration and,
   at send time, the push), no user-info, at most 2,048 bytes. An installation whose push service is
   private (a self-hosted ntfy) sets `CHOREGOS_PUSH_ALLOW_PRIVATE_ENDPOINTS=true`. Redirects are not
   followed.
8. **Standard cryptography, no new dependency.** RFC 8291 and RFC 8292 are implemented over
   `cryptography` (already locked): ECDH P-256, HKDF-SHA256, AES-128-GCM, ES256 JWT. The test decrypts
   what the sender produced with the device's side of RFC 8291 and checks the VAPID signature; the
   RFC 8291 appendix A vector is part of the suite.
9. **Configuration.** `CHOREGOS_PUSH_VAPID_PRIVATE_KEY` (a secret: the P-256 private key, PEM or the raw
   32 bytes in base64url), `CHOREGOS_PUSH_VAPID_SUBJECT` (`mailto:` or `https:`, which a push service may
   use to reach the operator). Without a key the instance sends nothing, `GET /push/vapid-public-key`
   answers 404 `push_not_configured` and a registration 409. The chart wires the key like its other
   secrets (`push.vapid.existingSecret`); `docs/deployment.md` says how to generate and rotate it
   (rotating the key makes every device register again: a subscription is bound to the key it was made
   with).
10. **A fake.** `CHOREGOS_FAKES=1` replaces the HTTP sender by `FakePushSender`, which records what it was
    asked to send and answers what the test scripts (201, 410, a timeout) (ADR 0005).
11. **Row-level security.** `push_subscriptions` belongs to a person, not to an organisation: it carries
    `user_id` and no `org_id`, like `user_sessions` (ADR 0026): a person reads and writes their own rows;
    the platform scope reads all (the sender runs there).

## Consequences

- A person with a phone and an ntfy distributor is told within seconds, and no vendor sees the content.
- The platform makes outgoing HTTPS calls to hosts a person chose: the checks of point 7 are the
  boundary, and the installation's egress policy still applies to the API's own traffic.
- Delivery is best effort. A stricter guarantee (an outbox table, retries) is a later decision if a
  deployment shows that pushes are lost in practice.

## Alternatives discarded

- **FCM/APNs directly**: a vendor SDK in the application and the content, or at least its existence, in
  the vendor's cloud; unusable on a phone without Google services.
- **Polling by the application**: a battery and latency cost, and no lock-screen notification when the
  application is not running.
- **A dependency (`pywebpush`)**: it pulls `requests` and `py-vapid` for 150 lines of cryptography this
  repository can test against the RFC's own vector.
