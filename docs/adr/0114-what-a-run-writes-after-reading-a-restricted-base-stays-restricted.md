# 0114 — What a run writes after reading a restricted knowledge base stays restricted

- **Status**: accepted, 2026-10-30 (follows [ADR 0097](0097-a-run-reads-a-restricted-base-on-behalf-of-its-initiator.md),
  finding #1042); the product owner chose the proposed answer to the three points below the same day
- **Concerns**: `WorkItem` (a new column), every route that serves a work item, its runs, artefacts and
  receipts (REST, MCP door, A2A door), memory writes at close-out, findings, tracker write-back and
  notifications, fan-out children

## Context

Since ADR 0097 a run is served a restricted base only on behalf of a person who reads it. What the run
writes afterwards is not: its summary, transcript, logs, diff and artefacts are read by anyone with
`PROJECT_READ` on the project (a `viewer` included), the A2A and MCP doors serve them to tokens, the
close-out writes a summary into the project's memory that later runs read, a finding's evidence goes to
the tracker and to Slack, the tracker receives each run's summary, a pull request's body is built from
the work item's documents, and a fan-out child's title comes from its parent's outputs. A person with no
grant on the HR base reads what the agent read there, one step removed. Nothing on a work item, a run or
an artefact carries any visibility today (finding #1042).

## Decision

1. **The work item is marked, not each output.** When `pin` serves a restricted base to one of its runs,
   the work item records the base's slug in `restricted_knowledge` (a list, only ever added to). The mark
   covers everything under the work item: its runs and their events, transcript, logs, diff, result and
   access chain, its artefacts, receipts, timeline, journey and pending question. Marking outputs one by
   one would miss the next one added; a work item is the unit people share, approve and follow.
2. **Reading a marked work item needs reading every base it names.** On top of `PROJECT_READ`, the
   caller must read each base in `restricted_knowledge` by the console's rule (`visible_to`: an
   administrator, an owner of the base's project, or a grant to the user, one of their groups or a
   project they are a member of). The check is evaluated against the bases **as they are now**: lifting
   `restricted` on a base, or granting it, opens the work items it marked; a base that no longer exists
   keeps them closed to everyone but administrators. The same check applies to deciding on a marked work
   item (an approval needs to read what is approved) and to the MCP and A2A doors, for the token's
   person; a token with no person behind it reads no marked work item.
3. **A marked work item writes no shared memory.** Its close-out summary, run lessons and findings
   evidence are not written into the project's, a shared or a global memory, and the run journal says it
   skipped them and why. Later runs would otherwise be served them with no person check.
4. **Children inherit the mark.** A fan-out child of a marked work item is born with its parent's
   `restricted_knowledge`, since its title and fields come from the parent's outputs.
5. **A finding filed by a run of a marked work item is marked the same way** and read by the same rule.
6. **What leaves the platform carries no content of a marked work item** (point A): the tracker
   receives the state and a link to the console instead of the run's summary, the agent's question or
   the finding's evidence; Slack and e-mail notifications the same; a pull request's body is the link,
   not the documents. Pushing the code itself stays allowed (point C).

## Points decided by the product owner

- **A. Outbound content.** Chosen: withheld as in point 6 (the tracker and Slack reach people the
  platform cannot check). Alternative: sent as today, the restriction holding inside the platform only.
- **B. Lists.** Chosen: a project member who does not read the base still sees the marked work item in
  lists (key, title as typed by its author, state, a "restricted" badge) and nothing more; the detail
  answers 404. A title an agent wrote (a fan-out child's, a finding's) is the run's content: the list
  shows "Restricted work item" instead (S42-11g). Alternative: hidden from lists as well, at the cost of work items that "disappear".
- **C. Code.** Chosen: the branch is still pushed and the pull request opened (with the link as its
  body): the code is the deliverable, and attaching a restricted base to a coding agent is the project's
  choice. Alternative: a marked work item opens no pull request and stops before delivery.

## Not decided here

- **Egress during the run.** An agent can still send what it read through a tool (`web_fetch`, a
  connector). That is the network policy's and the connector's business (ADR 0077 §3), not this ADR's.
- **Unmarking.** A work item is never unmarked by the platform; an administrator lifts the base's
  restriction or grants it, which is journalled.

## Consequences

- Project members without a grant lose sight of the content of work items that read a restricted base,
  including ones they started before a grant was revoked; an approval addressed to someone who cannot
  read the base waits for a person who can, or for an administrator.
- One column on `work_items` (a migration), one field on the `WorkItem` DTO (contract-change PR), one
  check shared by the routes listed above, each covered by a test that fails without it.
