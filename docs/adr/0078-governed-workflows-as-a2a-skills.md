# 0078 — Governed workflows served as A2A skills (inbound A2A)

- **Status**: accepted, 2026-10-22; S40-23 implements §1–4 and §6 partially: the opt-in, the card,
  `message/send`, `tasks/get`, `tasks/cancel`, the `a2a` scope, audit, rate limit, parts cap and
  the `a2a:` initiator (ADR 0077); S40-23c adds `message/stream` and `tasks/resubscribe`, S40-23d
  push notifications (§5), S40-23e the artefacts' content in parts (256 KiB each, 1 MiB per task,
  digest checked) and the card URL on the Integrations page (#757). File parts in a message stay
  refused: a work item has no store for a file it did not produce, and the project has no size limit
  to count it against
- **Concerns**: a new `/a2a` route at the root of the API process (beside `/mcp`), the workflow
  definitions (an opt-in per workflow), the work items service, the events stream, `chg_` token
  scopes; builds on [ADR 0030](0030-an-mcp-door-for-external-clients.md) (the MCP door, its tokens and
  guards), [ADR 0062](0062-remote-agents-over-a2a.md) (outbound A2A) and
  [ADR 0008](0008-idempotence-and-run-id.md) (deterministic ids)

## Context

ADR 0062 lets Choregos call a remote agent over A2A. The other direction does not exist: no route
serves an agent card (`apps/api` serves only the OIDC and `oauth-protected-resource` well-knowns),
and an external agent reaches Choregos only through the MCP door, which offers **tools**
(`create_work_item`, `get_work_item`…) and leaves the calling agent to poll and interpret states.

An agent-to-agent architecture reviewed on 2026-10-20 had its client poll a gateway for results.
A2A gives the caller a task with a lifecycle, streaming and push notifications; a governed workflow
already has that lifecycle, durably, in Temporal.

## Decision

1. **What is exposed: a workflow, never a bare agent.** A workflow definition opts in with
   `a2a: {skill: <id>, description, input_modes}`; nothing is exposed by default. An agent outside a
   workflow has no gates, no budget per work item and no human decision: it is not served.
2. **Where.** One agent per organisation: card at `/a2a/orgs/{org}/.well-known/agent-card.json`,
   JSON-RPC at `/a2a/orgs/{org}`, out of the OpenAPI document like `/mcp`. The card lists the opted-in
   workflows of the projects the caller may see as skills; protocol version as ADR 0062 pins it.
3. **Authentication: the MCP door's.** OAuth resource server (ADR 0030 §7) or a `chg_` token with a
   new scope `a2a`; a `*` token is refused, as on the door. The caller acts as the person or client
   the token belongs to; ADR 0077 records it as the work item's initiator.
4. **The task is a view of the work item.** `message/send` creates a work item through the same
   service as the door's `create_work_item`; its id is derived from `(org, client, messageId)`
   (ADR 0008), so a resent message is the same work item. `tasks/get`, `message/stream` and
   `tasks/resubscribe` read the work item and its events; the state lives in the workflow, never in
   the A2A layer. States map one way: a running stage is `working`, a pending human decision is
   `input-required` **with the console's `decision_url` and no way to decide over A2A** (ADR 0030 §5),
   done is `completed` with the work item's artefacts as parts, failed or cancelled are `failed` or
   `canceled`. `tasks/cancel` cancels the work item when the caller holds that right.
5. **Push notifications** to a URL the caller registers are signed (HMAC with a per-registration
   secret shown once), sent at least once after the event is persisted, with the event id as the
   receiver's idempotency key, and go through the organisation's egress
   allowlist (ADR 0066) like any platform call: a host the organisation has not opened is refused at
   registration.
6. **Guards: the door's.** Every call audited (`a2a.call`), rate-limited per token, message parts
   capped and wrapped as data, not instructions; a file part counts against the project's limits.

## Alternatives rejected

- **Each registry agent as an A2A agent.** It would let a caller drive an agent outside any workflow,
  without the guarantees that make it governed.
- **A2A state kept in its own store.** A second source of truth for a task that the workflow already
  holds durably; the reviewed design's per-agent checkpoint tables show where that leads.
- **Deciding through A2A.** A decision needs a re-authenticated person (ADR 0030 §5).

## Consequences

- A new protocol surface, tested like the door: a reference A2A client (test dependency only) sends
  a message, follows the task to `input-required`, sees the decision URL, and reads `completed`
  after the decision is taken in the console.
- The Integrations page gains the card URL per organisation.
- Not decided here: one card per project instead of per organisation; A2A's extended card for
  authenticated callers.
