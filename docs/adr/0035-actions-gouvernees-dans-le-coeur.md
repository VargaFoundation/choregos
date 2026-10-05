# 0035 — Governed actions move into the core and run in Temporal; each effect is recorded by key

- **Status**: accepted, 2026-10-05
- **Concerns**: a new `actions` table (taking over the ontology's `action_proposals`), a new
  `ActionWorkflow`, the interpreter (an action on a system transition, dates), the human request
  (a task with a form and evidence), the console's approval box; follows
  [ADR 0030](0030-une-porte-mcp-pour-les-clients-externes.md),
  [ADR 0033](0033-registre-d-agents-et-bibliotheque-de-skills.md) and
  [ADR 0034](0034-connecteurs-par-capacites.md)

## Context

The HR scenario — onboarding and offboarding — writes to systems that are not ours: create an Entra
account, add it to groups, order a laptop from a supplier's agent, enrol it in the device manager,
activate a badge, then undo all of it on departure. Each write must be **approved** when it is
sensitive, **proved**, **resumable** after a crash, and never done twice.

The ontology's actions (S15-03) already carry proposals, approvals, separation of duties and
evidence. But they run **inside the HTTP request** that approves them, they are proposed as
`agent:platform`, and nothing resumes them: a crash between two effects leaves half an account.

## Decision

1. **Actions move into the core**, in one table `actions` — `ontology`, `tool` (an MCP operation set
   to `approval`, ADR 0034) or `transition` (an effect of a workflow step). The ontology's
   `action_proposals` is migrated into it and dropped (`onto0003`): one owner for the schema
   (ADR 0026).
2. **An approved action runs in Temporal**, `ActionWorkflow` (`action-<id>`), never in the request:
   the HTTP answer leaves before any effect. Each effect is recorded under a key (`<action>:<n>`)
   before it is attempted and confirmed after: a retried activity sees the key and does not do it
   twice; a killed worker resumes at the next effect. A failed action runs its **compensations**,
   in reverse order, and says what it could not undo.
3. **An effect is a seam**, `declarer_un_effet(nom, …)`: idempotent by construction (create a user
   by UPN, add to a group accepting "already a member"), honouring `Retry-After`.
4. **A system transition can carry an action, at a date**: `Transition.action` and
   `not_before: "fields.date_arrivee - 10d"`. The timer is re-armed when the field changes; the
   guarantee `action_succeeded` closes the step. Under `workflow.patched`, with new histories.
5. **A human step can be a task**: `HumanRequestKind.TASK` with a form and an attestation — the UID of
   a badge, a tracking number — whose values flow into the next action's parameters.
6. **Approval needs a fresh authentication**, as every decision does (ADR 0030); of two concurrent
   approvals only one runs the action.

## Consequences

- The HR template `joiners-leavers` becomes possible end to end on fakes (Entra, MDM, carrier,
  badges, a supplier's agent served as an MCP server), and on the real systems later.
- An action's evidence file — parameters, approvals, effects, their keys and answers — is the audit
  an HR or security officer asks for, per person.
- Four replay histories at least (interpreter with actions and dates, `ActionWorkflow` with retries
  and compensation) join `tests/replay`.
