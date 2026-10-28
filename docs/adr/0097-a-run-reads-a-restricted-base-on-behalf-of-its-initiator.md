# 0097 — A run reads a restricted knowledge base on behalf of the person who started it

- **Status**: accepted, 2026-10-28 (product owner: "use the rights of the person who launches", proposal
  validated the same day)
- **Concerns**: `knowledge/access.py`, `knowledge/serving.py` (`pin`, `deliver`), the run's knowledge routes
  (`GET /internal/runs/{id}/knowledge`, `POST /internal/runs/{id}/knowledge/search`), the stage start of the
  orchestrator; amends [ADR 0081](0081-knowledge-base-rights.md) (point 2: "runs that the base is attached to
  are still served"); builds on [ADR 0077](0077-delegated-identity-between-steps.md) (the
  work item's initiator, copied onto each run)

## Context

ADR 0081 made a base `restricted`: people read it only through a grant. Runs were left out on purpose: "a
grant gives people a role; it never serves a run". A restricted base attached to an agent is therefore
served to **every** run of that agent in the projects the base reaches, whoever started the work item. A
person with no grant on an HR base can start a work item whose agent reads that base and writes what it
read into a result the person reads. The restriction holds for the console and leaks through the agent.

The product owner asked for the agent to act with the rights of the person who launched it.

## Decision

1. **A run acts on behalf of its initiator.** The initiator is the work item's `initiated_by` (ADR 0077 §2),
   copied onto the run at stage start. It names a person for `person:<email>`, `mcp:<email>` and
   `trial:<email>`. A fan-out child (`work_item:<key>`) acts for its parent's initiator, followed up to five
   levels within the project. Every other kind (`schedule`, `tracker`, `finding`, `a2a`, `eval`, `demo`,
   `system`) names no person.
2. **A restricted base is served only if that person may read it.** The run is served the base when the
   base is served to its project (ADR 0081, unchanged) **and**, for a restricted base, when the initiator
   reads it through the same rule as the console (`visible_to`: an administrator, an owner of the owning
   project, or a grant to the user, one of their groups, or a project they are a member of). A run with no
   person behind it is never served a restricted base. A base that is not restricted is served as before:
   whoever may start a work item in a project already reads the bases that project reaches.
3. **Checked when the run is prepared and at every read.** `pin` refuses at stage start, and the run stops
   with a sentence that names the base and the initiator, as for a base not served to the project. The
   run's `files` delivery and its `knowledge_search` check again, so a grant revoked while a run is in
   flight holds at its next read.
4. **The person's rights are read from the database, not from a token.** The run carries no credential of
   the person; the API rebuilds the person's roles in the run's organisation (memberships and IdP groups)
   when it decides, without widening the request's row-level-security scope.

## Not decided here (follow-ups)

- **Outputs inherit the restriction.** What a run writes after reading a restricted base (its result, an
  artefact, a memory) is visible to everyone who reads the work item. Marking those outputs restricted
  needs a visibility on work items and artefacts that the platform does not have; finding issue opened.
- **Connectors called with the person's own credential.** That is ADR 0077 point 3 (`on_behalf_of`, RFC 8693
  token exchange at call time, #756), accepted and not implemented. A knowledge base synced by a service
  account is covered by point 2 above, not by a token exchange.
- **A schedule that acts for a person.** A schedule is declared in the project's configuration and names
  nobody; a field `on_behalf_of` on a schedule would let its runs read restricted bases. Not added until
  asked for.

## Consequences

- A scheduled or tracker-triggered work item whose agent reads a restricted base now stops at stage start,
  where it used to be served. The sentence says why; the fix is to lift `restricted`, or to start the work
  item as a person who reads the base.
- No migration and no new route; the contract's descriptions of `restricted` change (contract-change PR).
