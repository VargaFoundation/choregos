# 0131 — An application calls under a machine account

- **Status**: proposed, 2026-11-09; S62-01 implements it
- **Concerns**: `/projects/{id}/machine-accounts`, the identity of a `chg_` token, the A2A door's
  budget; builds on [ADR 0030](0030-an-mcp-door-for-external-clients.md),
  [ADR 0033](0033-agent-registry-and-skill-library.md),
  [ADR 0077](0077-delegated-identity-between-steps.md) and
  [ADR 0078](0078-governed-workflows-as-a2a-skills.md)

## Context

An application that starts workflows over the A2A door holds a token, and a token belongs to a person.
The application then acts under that person's name, shares their 50 writes a day with their assistant,
and depends on them staying. The only caller with no person behind it is an HTTP trigger
([ADR 0113](0113-a-workflow-starts-from-a-trigger.md)), which opens a work item and follows nothing.

`User.is_service` exists and nothing sets it. No ADR decides an inbound machine identity.

## Decision

1. **A machine account belongs to one project.** A person who may manage the project's members creates
   it (`member:manage`), from a person's credential: a machine account does not create another. An
   application working on several projects holds several accounts, each with exactly the rights of one.
2. **It is a user of its own kind.** A `users` row with `is_service`, and a `Membership` of role
   `developer` on the project, `source: machine`. Its principal has `kind: machine`, so every gate that
   asks for a person (`kind != "user"`) refuses it without further code. Its address is on the reserved
   `.invalid` domain: no mail reaches it and no identity provider can vouch for it; the OAuth path of
   the doors refuses it all the same.
3. **It holds `a2a` tokens only.** Bound to its project, with an expiry of 365 days at most, shown once.
   Several may live together, which is how a token is rotated. The REST API and the MCP door refuse them.
   Opening those is not decided here.
4. **It never decides.** The door takes no decision (ADR 0078), its tokens open nothing else, and it has
   no group, so no approval rule can name it. It is left out of the members list.
5. **Its budget is its own**: `writes_per_day`, 500 by default, 10,000 at most, counted on the account
   like a person's on them. The per-token call rate does not change.
6. **The initiator says so**: `machine:<address>`, a kind [ADR 0077](0077-delegated-identity-between-steps.md)
   did not have. No person stands behind it, so a run acts for nobody: a restricted base and an
   on-behalf call are refused as for a schedule.
7. **It outlives whoever created it, and it ends explicitly.** Removing the account deletes its tokens
   and its membership and keeps the row, so the journal still reads. Its slug is not reused.

## Alternatives rejected

- **A token with no user.** `api_tokens.user_id` is not nullable, row-level security filters on it, and
  every reader builds a principal from it.
- **The `SYSTEM` principal, as a trigger uses.** It holds no role, so the door would need its own rights
  check, and the journal could not tell two applications apart.
- **One account across an organisation.** Its rights would be the union of what its applications need.

## Consequences

- An application no longer depends on a person, and the journal names it.
- Workload identity (OAuth client credentials, SPIFFE) stays open: it would replace how the account
  proves itself, not what the account is.
