# 0044 — People are declared in the workflow; the policy's `approvals` and human review are not guards

- **Status**: accepted, 2026-10-10
- **Concerns**: `choregos_core.policy` (`PolicyEngine`, `policy_warnings`), the `solo`, `team` and
  `regulated` presets, the `github-software-delivery` template, `PUT /projects/{id}/policy`, the
  console's settings screen; follows [ADR 0041](0041-l-approbation-suit-ce-que-le-lot-emporte.md)

## Context

A policy carries `approvals.spec|merge|prod|scope_change` (`always`, `never`, `by_size`, `by_risk`,
with a group and a timeout) and `review.require_human_for_risk`. The presets filled them in — "a
human approves the spec and the merge", "prod: always" — and the console listed "approvals" among
what "the orchestrator applies". Finding #286 measured it: `PolicyEngine.approval_for` and
`PolicyEngine.human_review_required` were called by nothing in the API or the orchestrator. The only
human gates that exist are the ones a **workflow** declares (a transition `by` a human actor,
`train.approval`, ADR 0041) and the release train's `release_train.<env>.approval`.

Two ways out: enforce them (the interpreter inserts a human request before the matching transition),
or stop pretending. Enforcing them would put a second source of human gates next to the workflow,
with its own vocabulary (`spec`, `merge`) that a workflow of another trade does not have (ADR 0012,
ADR 0029).

## Decision

1. **People are declared in the workflow.** A policy says how much, how many times and where; it
   does not say who approves a work item. The release train's approval stays in the policy, because
   it governs a departure, not a work item.
2. **The contract does not change** (ADR 0001): `approvals.*` and `review.require_human_for_risk`
   stay in `policy.schema.json`, so every policy written before stays valid.
3. **The platform says they are not enforced.** `policy_warnings(policy)` names each key that
   promises a guard — an `approvals` rule other than `required: never`, a non-empty
   `review.require_human_for_risk` written by the text (the contract's `[high]` default is not
   counted). The API logs one `policy.not_enforced` warning per key when a policy is saved and when
   a project is born with its template's policy. `PolicyDto` has no warnings field; adding one is a
   contract change, left for later.
4. **The presets and the shipped template no longer write them.** The console's settings screen no
   longer lists approvals among what is applied, and says where to declare a person instead.
5. **`PolicyEngine.approval_for`, `human_review_required` and `ApprovalDecision` are removed**:
   nothing called them, and an engine that answers "approval required" without anything asking is
   the phantom guard itself. `review.cross_backend` is enforced and stays.

## Consequences

- A project on `team` or `regulated` that relied on "a human approves the merge" never had that
  gate; it now reads so. To get it, its workflow names a human actor on the transition.
- The warning is in the logs, not in the console's editor: the editor shows only what the server
  returns, and the policy endpoint returns no warnings yet.
- Removing the keys from the schema, or returning the warnings with the policy, is a later
  `contract-change`.

## Alternatives discarded

- **Enforcing `approvals.*` in the interpreter**: two places to declare one human gate, with names
  (`spec`, `merge`) that belong to software delivery only.
- **Removing the keys from the schema now**: every existing policy that sets them would be refused
  on its next save, for a field that changed nothing.
