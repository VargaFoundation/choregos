# 0077 — Delegated identity between the steps of a work item

- **Status**: proposed, 2026-10-20 — not implemented (#756)
- **Concerns**: the run token (`security.py`, `mint_run_token`), the `runs` table, the run's access
  record (`GET /runs/{id}/access`), the tool broker (`services/courtier.py`), the connector types of
  the registry (`choregos_adapters`); builds on [ADR 0021](0021-spiffe-behind-the-run-token.md) (the
  run token and SPIFFE), [ADR 0034](0034-connectors-by-capability.md) (the broker),
  [ADR 0062](0062-remote-agents-over-a2a.md) (a remote agent that runs as the user who authorised)
  and [ADR 0075](0075-an-agent-reads-several-memories.md) (an agent's own memory)

## Context

On 2026-10-20 Vincent reviewed an agent-to-agent architecture where agent A delegates to agent B
by itself: A exchanges its token for one scoped to B (OAuth 2.0 token exchange, RFC 8693), puts it
in B's queue message, and B checks it against an ACL cached from an agent registry. B then acts on
A's behalf and cannot read A's data partitions.

What Choregos does today, checked in the code:

1. **No agent calls another.** A step is started by the workflow interpreter, never by an agent;
   the next step of a work item is a transition of its workflow (ADR 0003). There is no code path
   by which a run starts, or addresses, another run.
2. **The run token is a handle, not a set of rights.** It carries `sub = run_id`, the project and
   the work item (`mint_run_token`), nothing else. What a run may call is derived **server-side**
   from the run row at every request: its agent version's selection, the organisation's and the
   project's operation policies, the project's groups (`outils_du_courtier`). A stolen run token
   reaches at most what that run could reach, until it expires (ADR 0021 lists the expiry and the
   revocation debt).
3. **Downstream systems see the organisation, never a person.** A connector call uses the
   connector's credential, resolved by the broker (ADR 0034). The one exception is a remote A2A
   agent such as Rovo (ADR 0062), whose credential is the authorising user's own OAuth token.
4. **The chain is not recorded.** A run knows its work item and its agent; the access record does
   not say who started the work item, nor which earlier run produced the inputs a step reads.

So the reviewed architecture's token exchange answers a problem Choregos does not have (an agent
choosing to call another), and does not answer one it does have: a system downstream of a connector
cannot tell *which person* an agent acted for, and the record cannot show the chain.

## Decision (proposed)

1. **Delegation stays orchestrated.** An agent never mints, holds or passes a credential for another
   agent. Handing work from A to B is a workflow transition; B's run gets its own run token, minted
   by the API at B's stage start, exactly as today. No inter-agent ACL and no registry cache: the
   right to call is decided at each request from the database (point 2 above), so a revocation holds
   at the next call.
2. **A run records its chain.** `runs` gains `initiated_by` (the principal that started the work
   item: a person's subject, a tracker event, a schedule, an external client of the MCP or A2A door)
   and `inputs_from` (the run ids whose outputs the step's input declares). The access record shows
   the chain `initiator → run A → run B` with the agent version of each run. Recorded at stage start,
   from data the orchestrator already holds; never sent by the agent.
3. **On-behalf-of only at the edge, only when the connector asks for it.** A connector type may
   declare `identity: on_behalf_of` (default: `service`, today's behaviour). For such a connector the
   broker performs an RFC 8693 token exchange **at call time**: subject = the work item's initiator
   (only when it is a person who signed in through the identity provider), actor = the run
   (`act: {sub: "run:<id>", agent: "<slug>@<version>"}`), audience = the connector, scopes = the
   operation's. The exchanged token is used for that one call and never stored, logged or returned
   to the run. An initiator that is not a person, or an exchange the identity provider refuses, is a
   tool error that says so — never a silent fallback to the service credential.
4. **Partitions are what the step declares.** B reads A's outputs only through its declared stage
   inputs; A's own memory (`own`, ADR 0075) stays A's. This is today's behaviour for memories; the
   story that implements point 2 checks it for artefacts and adds the test that fails if a step can
   read an artefact it did not declare.

## Alternatives rejected

- **Agent-held exchanged tokens (the reviewed design).** A token at rest in a queue message outlives
  its purpose or forces a long TTL; the agent's code carries its own authorisation; choreography
  leaves no one who knows the work item's state (ADR 0003).
- **Delegating the run token itself to the connector.** It would reach a third party, which ADR 0021
  and `test_le_jeton_du_run_ne_sort_jamais_vers_un_mcp_distant` forbid.
- **Exchanging on every call by default.** Most connectors are organisation systems with a service
  account; asking the identity provider for a token per call would add latency and a hard dependency
  for no gain.

## Consequences

- One migration (`runs.initiated_by`, `runs.inputs_from`), a field on the access record, a
  connector-type declaration, and an exchange in the broker behind it; no contract change for the
  run token.
- The identity provider must allow token exchange for the platform's client (Keycloak: the token
  exchange feature and a permission per target client). Without it, `on_behalf_of` connectors are
  refused at creation, with the reason.
- Not decided here: SPIFFE as the actor's identity (ADR 0021 stays proposed); delegation across
  organisations.
