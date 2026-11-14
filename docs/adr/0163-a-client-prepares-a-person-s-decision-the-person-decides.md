# 0163 — A client prepares a person's decision; the person decides

- **Status**: proposed, 2026-11-14 (the outposts plan's lot L2, approved by the product owner on 2026-11-11;
  rebuilt on the assistants stream S63, which was on `main` by then); S65-20, S65-21a, S65-21 and S65-22
  implement it
- **Concerns**: `services/agent_governance.py` and `services/agents.py` (who may carry a step), the MCP door
  (`mcp/tools.py`), `human_requests.draft` (a migration), `openapi.yaml` (`HumanRequest.draft`,
  `DecisionDraft`, `DELETE /work-items/{id}/human-requests/{request_id}/draft`), the decision route's audit,
  the console's awaited decision; applies [ADR 0030](0030-an-mcp-door-for-external-clients.md) point 5;
  builds on [ADR 0033](0033-agent-registry-and-skill-library.md),
  [ADR 0035](0035-governed-actions-in-the-core.md) point 5,
  [ADR 0139](0139-an-assistant-through-the-mcp-door-and-an-embedded-component.md) points 3-5 and
  [ADR 0153](0153-a-human-step-may-be-addressed-to-a-person.md); stream S65

## Context

A person who uses an agent of their own — Claude Code on their workstation, an assistant reached from
claude.ai on their phone — wants it to do the work of the steps that wait for *them*: read what a task asks,
gather what it needs, fill the form, write the answer. Two ways were open.

The first was to let a workflow name that agent as a step's actor: an *inbox* the external client would
poll, whose outputs would move the work item. The outposts plan refused it (its §2.3): nothing of such an
agent is measured, none of the five components of an agent version (ADR 0127) is declared, and its outputs
would choose the workflow's transitions — the client would decide, which ADR 0030 point 5 forbids. Reading the
code showed the hole was already open: an agent of kind `external` (a client of the door, ADR 0033) could be
named by a workflow, the publication accepted it, and `resolve_agent` handed the run its version as if the
platform ran it — with no runtime, an ACP run. A test written before this ADR proved both.

The second is that the step stays a person's (ADR 0035 point 5, ADR 0153) and the client *prepares* the
person's decision: it reads the waiting request, deposits a draft, and the person decides in the console.
The assistants stream already settled how a client's write becomes a person's gesture: a proposal that
executes nothing, carried to the client as `pending_actions[{id, summary, confirm_url}]`, decided by the
person under their own session (ADR 0139 points 3-5, S63-10; ADR 0154 §6-7).

## Decision

1. **An external agent never carries a step.** A workflow that names an agent of kind `external` as an
   actor is refused at publication (422, the agent, its scope and kind named, and the fix: name a person),
   and a run that resolves one fails before it starts (`AgentUnavailable`, not retried; the lifecycle's own
   refusals — draft, retired, expired — are told first). `internal` and `remote` agents (ADR 0062) are
   unchanged. A project may still pin an external agent: a pin names no step, and an agent's retirement
   reads its pins.

2. **The step's actor stays a person; their client prepares the decision.** The MCP door gains two tools:
   `get_pending_decision(work_item?)` reads the caller's own waiting requests — without an argument the
   list, with one the full context of that request: what it asks (question, summary, title, instructions,
   the task's form and the sentence it makes one attest), who it is addressed to, its due date, the work
   item's title, body, fields and state, the draft already deposited, and the console link where it is
   decided; and `draft_decision(work_item, kind, answer?, reason?, values?, note?)` deposits a draft for
   that request. Neither decides: the request stays waiting, nothing reaches the workflow's interpreter, no
   `workitem.decision` is written. The tool names stay outside `decid|approv|reject|answer`
   (ADR 0030 point 5).

3. **The caller's own request**, and only that. A waiting request on a work item the person may read
   (ADR 0114), in a project where they hold `item:decide`, that ADR 0153's rules let them answer
   *themselves*: the person it is addressed to, a member of the addressed group when the group may answer,
   anyone of that rank when the step names nobody — never through an organisation administrator's override.
   Any other request — another person's, a decided one, one in a project the person cannot read — answers
   *not found*, the same words as a request that does not exist.

4. **What a draft holds.** `kind` among what the request accepts — `approve` or `reject` for an approval or
   a scope change, `answer` for a question, `complete` or `reject` for a task — with the `answer`, the
   `reason` or the task's `values`, and a `note` from the client (why it proposes this; data, shown as
   such). Values are checked against the task's form when the draft is made — a key the form does not
   declare or a value its schema refuses is refused — but a draft may leave required fields empty for the
   person to fill. A draft never attests (the attestation is the person's sentence, ticked by them) and
   never grants paths. The platform adds a readable `summary`, `by` (the person whose client drafted it),
   `via` (the door, the client — the token's name or the OAuth client — the external agent it embodies when
   it is attached to one, and its user agent) and `at`.

5. **Where it lives: on the request.** `human_requests.draft`, one nullable JSON column: one draft per
   request, a new one replaces it, a decided request takes none. It is **not** a `ConversationAction`, the
   pending action of S63-10, for four reasons: that row belongs to a conversation (a foreign key the
   person's conversation owns, listed per conversation) and a client of the door has none; confirming it
   makes the broker call a source's operation, whereas a decision is not a source write — it passes the
   decision route's rank, addressee and gesture checks and is delivered to the interpreter, and that route
   must stay the one path; its life is a day, whereas a request waits as long as its step and its SLA say;
   and the person reads a draft where they decide, on the work item. Nor is it a table of its own: one
   draft per request needs no rows of its own, and the request's row-level security already covers it.
   What is taken from S63 is the model, unchanged: a proposal executes nothing; the client is answered
   `pending_actions: [{id, summary, confirm_url}]` (the id is the request's, the link the console's awaited
   decision), so a client that already handles `ask_assistant`'s proposals handles this one; only the
   person, under their own session, decides.

6. **The guards of a write.** `draft_decision` needs `mcp:write` and is announced only to a person who
   holds `item:decide` in a project they can read; an external agent's version must name it, like any tool
   of the door (ADR 0033). It counts in the person's fifty writes a day through the door; every call is
   audited as `mcp.call` and a deposited draft as `workitem.decision.draft` (the work item, the request,
   the kind, `via`). `get_pending_decision` needs `mcp:read`.

7. **The person takes it or discards it, in the console.** The awaited decision shows the draft, who made
   it and through what, and its note. *Take* fills the decision's form with it; the person still presses the
   decision's own button, ticks the attestation themselves, and the decision is the existing
   `POST /work-items/{id}/decisions` under their session. *Discard* is
   `DELETE /work-items/{id}/human-requests/{request_id}/draft`, for whoever may answer the request (the
   rights of the decision route), audited `workitem.decision.draft.discard`; a decided request is a 409.
   The decision's audit says whether a draft was there and whether the decision is the draft's
   (`draft: same` or `changed`), so how often a draft is taken is measured. The draft stays on the decided
   request as what was proposed.

8. **Nothing under `workflows/` changes.** The draft is a column the API reads and writes; the orchestrator
   never reads it, no activity or signal is added, and no history replays differently.

## Alternatives rejected

- **An external agent as a step's actor** (an inbox the client polls): its outputs would choose the
  transitions, nothing of it is measured, and none of its components is declared — the hole point 1 closes.
- **A draft as a `ConversationAction`**, with a nullable conversation and a `decision` kind: it would bend
  the row's owner, its listing, its expiry and its confirmation, which executes through the broker, into a
  second decision path (point 5).
- **A `decision_drafts` table**, as the plan first wrote: rows of its own, a policy of its own and a join,
  for one draft per request.
- **A decision through the door with a confirmation argument**: refused by ADR 0030 point 5 and ADR 0139
  point 4.
- **A flag to switch point 1 off.** The plan proposed one in case a deployment's workflow already names an
  external agent. Such a workflow runs a client of the door as if the platform ran it, which is the defect;
  an operator checks for one before upgrading with the query of `docs/deployment.md` ("Upgrading past
  0.27.3"), and a refusal tells them which agent and what to do.

## Consequences

- A contract change (S65-21a): `HumanRequest.draft` and `DecisionDraft`, and the discard route.
- A migration: `human_requests.draft`, nullable; downgrade drops it.
- A deployment whose workflow names an external agent sees that workflow's next publication refused, and a
  run of it fail with the agent named, until a person is named for the step.
- An assistant that runs inside the platform drafts only when it reaches the door as a client. Offering
  `draft_decision` to an assistant's own turns, and telling a person when a draft is deposited for them,
  are left for when someone asks.
