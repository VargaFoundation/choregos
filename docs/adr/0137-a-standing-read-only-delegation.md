# 0137 — A standing, read-only, revocable delegation lets a task read on a person's behalf

- **Status**: proposed, 2026-11-10; acceptance waits for the works council's opinion, the data protection
  impact assessment and the security review of the delegation (S63-14)
- **Concerns**: an assistant's `tasks`, new tables `task_subscriptions` and `delegations`, the schedules of
  workflows (S38-11) made per subscriber, a `poll` trigger, task delivery (inbox, email, Teams, webhook),
  the routes `/me/assistants/{org}/{slug}/tasks/*` and `/me/delegations/*`; amends
  [ADR 0107](0107-an-agent-acts-under-an-identity.md) point 3; builds on
  [ADR 0049](0049-an-artifact-is-typed-stored-and-traced.md), [ADR 0113](0113-a-workflow-starts-from-a-trigger.md),
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md) and
  [ADR 0135](0135-a-tool-call-may-carry-the-person-s-identity.md)

## Context

ADR 0107 point 3 forbids acting on a person's behalf in work no signed-in person started: a schedule, a
tracker event or a sync runs under an assigned identity. An assistant's background tasks — a brief the day
before a meeting, a morning digest — must read sources as the person, while the person is not signed in.
The market hits the same wall: a platform that imposes the user's identity on a scheduled task fails it.

## Decision

1. **A task is a workflow of the plugin run in the subscriber's personal space.** One work item per run
   (`tracker: internal`), the subscriber as initiator and the task named on the work item; its outputs are
   artifacts (ADR 0049) delivered by the task's `deliver` list (`inbox` always; `email`, `teams` through
   the notification connector; `webhook` to a host such as an internal application). Budget: the
   subscriber's month, then the installation's.
2. **Triggers.** `on_demand` (a button, or a `run_task` tool offered in the conversation: the session's
   identity, no delegation); `schedule` (a cron per subscriber, in their time zone); `poll` (a light cron
   that calls one read operation and starts the workflow only when a JSON-pointer condition holds, with an
   idempotency key per object so that one object never starts twice); `trigger` (an event of ADR 0113
   routed to a subscriber, later).
3. **A delegation is an exception to ADR 0107 point 3, and it is bounded.**
   - **Explicit**: given per task, after the person has seen the exact list of needs and operations read.
   - **Read only**: only `access: read` needs; a write in a delegated task becomes a proposal the person
     confirms.
   - **Bounded in time**: 90 days by default, the installation may lower the maximum; a reminder before it
     ends.
   - **Revocable at once**: revocation cuts the derived tokens and the next call fails.
   - **Tokens**: for `user_consent`, the connected account's refresh token; for `user_exchange`, an offline
     token of the identity provider (`offline_access`), sealed, restricted to the exchange client, revoked
     with the delegation.
   - **Suspended automatically** when the person leaves the audience, is disabled at the provider, or when
     the manifest changes the list of needs the task reads (`manifest_digest`): a new agreement is needed.
4. **Every delegated read is journaled with its delegation**; the person sees what their tasks read.
5. **A task with `delegation: none` reads only `service` needs.** A public data poll (a price crossing a
   threshold) needs no delegation.

## Alternatives rejected

- **Running scheduled tasks under the connector's account.** The source would no longer decide what the
  person may read.
- **Waiting for the person to sign in.** A brief delivered after the meeting is useless.
- **A delegation for all of an assistant's tasks at once.** The person agrees to a list of reads they can see;
  a new task is a new agreement.

## Consequences

- A stolen offline token is a standing read in someone's name: a dedicated security review of this ADR and
  of ADR 0135 is a gate before production, and delegations are not offered before the second delivery lot.
- Schedules become per subscriber: S38-11's one window per run is kept, keyed by subscription.
