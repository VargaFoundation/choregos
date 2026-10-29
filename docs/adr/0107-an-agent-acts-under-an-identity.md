# 0107 — An agent acts under an identity: assigned, its own, or on behalf of a person

- **Status**: accepted, 2026-10-28 (product owner's review of the same day: "an agent should be able
  to act with a service account, with a person's identity, or with an account acting on a person's
  behalf"; the decisions below were taken with him and the record accepted the same evening)
- **Concerns**: the tool broker (`services/broker.py`) and its MCP connectors, the connector types of
  the registry (`choregos_adapters`), the agent version's `mcp_servers`, the orchestrator's tracker
  activities (`activities/tracker.py`), the run record (`runs.initiated_by`, `inputs_from`, the access
  record's `chain`); builds on [ADR 0034](0034-connectors-by-capability.md) (the broker, a write is a
  governed action), [ADR 0035](0035-governed-actions-in-the-core.md),
  [ADR 0062](0062-remote-agents-over-a2a.md) (a remote agent that runs as the user who authorised),
  [ADR 0077](0077-delegated-identity-between-steps.md) (delegated identity between steps) and
  [ADR 0097](0097-a-run-reads-a-restricted-base-on-behalf-of-its-initiator.md); amends ADR 0077 point 1

## Context

The product owner's model of an agent, reviewed on 2026-10-28:

1. an agent acts under an identity: its own, derived from a person, or assigned;
2. it uses a model (directly or through a gateway), the MCP tools it is given, and knowledge bases;
3. it runs on a secure runtime;
4. it may launch other agents, which run with the parent's identity.

What the code does today:

- **Two writers to a tracker, both under an organisation account.** The orchestrator keeps the ticket
  in step with the workflow (state, the single status comment, the Cost/Size/Risk/Run fields, the
  human-request comment, the close-out, findings) through the project's tracker connector, with that
  connector's credential (Jira: `email` + `api_token`; GitHub: the App installation). An agent writes
  only through the broker, to the MCP connectors its version names, with the organisation connector's
  key resolved in the API; a write is a governed action. Neither path ever acts as a person; the
  console says neither.
- **On behalf of a person exists in two narrow places**: a remote A2A agent runs as the user who
  authorised it (ADR 0062), and a run reads a restricted knowledge base on behalf of the person who
  started the work item (ADR 0097). ADR 0077 point 3 (an RFC 8693 exchange at the edge) is accepted and
  not implemented (#756).
- **No agent launches another** (ADR 0077 point 1): handing work on is a workflow transition, or a
  fan-out (ADR 0057).
- **A work item started by a webhook names no person**: its initiator is `tracker:<source>`. The Jira
  actor in the payload is not authenticated by Choregos: Jira Cloud does not sign its webhooks and the
  shared secret is the platform's.

## Decision

1. **An agent acts under one of three identities, chosen per connector.**
   - **Assigned** — a service account configured on the connector (today's behaviour, the default).
   - **Its own** — the agent's principal, `agent:<slug>@<version>`, mapped per connector to an
     account that exists for that agent at the target (an Entra agent identity, an Atlassian service
     account per agent). Downstream sees the agent, not the organisation's shared account.
   - **On behalf of a person** — the exchange of ADR 0077 point 3, at call time: subject = the work
     item's initiator, actor = the run (`act: {sub: "run:<id>", agent: "<slug>@<version>"}`), the
     audience and scopes of the one operation; never stored, logged or handed to the run.
   A connector type declares the modes it supports; the agent version's `mcp_servers` entry picks one
   of them; the organisation may forbid a mode per connector.
2. **Identity is not authorisation.** Whatever the identity, the call still passes the operation's
   policy (`allowed`, `approval`, `forbidden`), the project's groups and the run's budget. What a call
   may do is the intersection of what the identity may do at the target and what Choregos allows.
3. **On behalf of a person only when a person signed in started the work.** The initiator must be
   `person:`, `mcp:` or the inbound A2A door — a principal authenticated by the identity provider. A
   work item started by a tracker event, a schedule or a sync runs its agent under an assigned or its
   own identity. Acting as the Jira user named in a webhook is refused: anyone who can forge the
   delivery would act as that person, without their consent.
4. **The workflow's bookkeeping stays with the platform, under the project tracker connector's
   account, and is shown.** Mirroring the state, the status comment, the fields and the close-out must
   happen when the agent fails, replay without a second effect (ADR 0008) and never depend on a model
   choosing to call a tool. Each bookkeeping write records the connector and the account it went out
   under, and the console shows them on the work item. The agent's **business actions** — commenting
   its analysis, linking a duplicate, moving a ticket because of what it found — go through its MCP
   tools under its identity.
5. **An agent may request other agents; the platform runs them.** A broker tool, `request_agent`,
   asks for a child run of a named agent with an input. The platform starts it as a run of the same
   work item: replayable, budgeted against the parent, recorded in `inputs_from` and the chain. The
   child acts under the parent's identity, **attenuated**: its tools are the intersection of the
   parent's and of the child version's selection; depth is bounded (2 by default); the child never
   receives a credential from the parent, and no agent ever holds one. This amends ADR 0077 point 1
   ("no agent calls another"): the agent asks, the orchestrator decides and runs.
6. **The model goes through the gateway.** A direct model call measures no cost and enforces no cap;
   `gateway: direct` stays refused in staging and prod.
7. **No credential enters the agent's runtime.** The pod holds its run token and its gateway key, as
   today (ADR 0021, ADR 0094). The service account's key, the agent's own account and the exchanged
   token of a person are used by the platform for one call, server-side. A prompt injection can make
   an agent misuse its tools within their policy; it cannot carry a person's identity out of the pod.
8. **Every call says who it was for.** The audit record of a connector call carries
   `as: assigned:<account> | agent:<slug>@<version> | person:<subject> via run:<id>`; revoking a
   person's consent or an agent's account stops the next call.

## Implementation (stories, in order)

1. Who writes, shown: the account on the tracker connector card, "Choregos writes as …" on the work
   item, the chain and the agent's MCP connectors on the run, the connector and account on each
   bookkeeping write (contract change: `initiated_by` on the work item, the agent on the run, a `via`
   on tracker timeline entries).
2. Connector identity modes: the declaration on connector types, the choice on `mcp_servers`, the
   `as` on audit records; assigned and own first.
3. On behalf of a person: the RFC 8693 exchange (#756), Microsoft Graph first (it supports
   on-behalf-of); Atlassian needs each person's OAuth consent (the A2A credential of ADR 0062);
   GitHub user-to-server tokens of the App.
4. `request_agent`: the broker tool, the child run, the attenuation and the depth bound.

## Alternatives rejected

- **All tracker writes through the agent's MCP tools.** The ticket's state would depend on a model
  calling a tool, and on its run succeeding; a replay could write twice.
- **On behalf of the Jira actor of a webhook.** No authentication, no consent (point 3).
- **A child inheriting the parent's full rights.** A child has other instructions and maybe another
  model or an external agent; carrying everything the parent could do makes it a confused deputy.
- **Handing the person's token to the run.** It would sit in the pod, where an injected prompt can
  reach it (ADR 0077, ADR 0021).

## Consequences

- An identity per agent at a target system is an account someone creates and pays for; assigned stays
  the default so nothing is needed to start.
- On behalf of a person depends on what each target system supports; where it supports nothing, the
  mode is not offered for that connector type, with the reason.
- The console must say, wherever a write happens, which connector and which identity it used.
