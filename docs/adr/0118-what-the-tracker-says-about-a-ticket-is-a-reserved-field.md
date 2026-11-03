# 0118 — What the tracker says about a ticket is a reserved field: `fields.tracker`

- **Status**: accepted, 2026-11-02 — S51-04. Extends [ADR 0031](0031-several-workflows-per-project.md)
  (a work item's fields, validated by its workflow's `metadata.inputs`)
- **Concerns**: `WorkItem.fields`, `choregos_core.domain.TRACKER_FIELD`,
  `choregos_adapters.tracker.ticket_fields`, the Jira and plugin tracker adapters, `services/routage.py`
  (`Naissance.tracker`, `refuse_reserved_fields`, `valider_les_champs`), `routers/webhooks.py`
  (`_dispatch(tracker_of=…)`), `activities/tracker.py` (`reconcile_tracker`), `AgentSampleInput.fields`

## Context

A Jira issue knows who filed it, who created it, its labels and the answers of its form (custom
fields). The adapter fetched `*all` and kept a display name as `author`; ingestion then dropped
`author` and `fields`, and labels survived only for routing. An agent asked to check "the requester
leads this project" never saw who the requester was. A team builds on the same data in parallel
(`diamctl project access --from-choregos`), so its shape must be fixed and stated.

## Decision

1. The work item keeps what the tracker says as **one object under one reserved field**,
   `fields["tracker"]`: `{kind, key, reporter, creator, labels, answers}`. `reporter` and `creator` are
   `{account_id, display_name, email}`, each `null` when the tracker is silent; an email is the one the
   tracker returned, never a guess. `answers` maps each non-empty custom field's **display name** to
   its value **as text** (an option's value, a user's name, a list joined with `, `, rich text as
   text), each at most 2000 characters, at most 100 answers and 20 000 characters in all. `kind` is
   `jira`, or a plugin tracker server's name (its `author` is the reporter's display name, its
   `fields` the answers, its creator empty).
2. **Only the tracker writes it.** A birth from a person, the MCP or A2A door, a schedule or a fan-out
   that carries `fields.tracker` is refused (422), and so is `PATCH /work-items/{id}` or a task's
   values; a plugin server's own `fields.tracker` is dropped. The poll writes it when it takes an
   issue on; a webhook delivery (Jira, or a plugin server) **re-reads** the issue and replaces it.
3. **It is not the workflow's.** `valider_les_champs` leaves it out, so `additionalProperties: false`
   still accepts a ticket filed from Jira; a workflow may not declare an input (or a required field)
   named `tracker` (`inputs.reserved`).
4. **It is data.** It reaches agents as `ticket.fields.tracker` and jobs in `.choregos/fields.json`,
   as everything else in `fields`. The catalogue's context prints it as one line of JSON in a section
   that says it is the requester's text, never an instruction. Eval cases and trials may give a
   sample's `fields`; without them, `ticket.fields` renders `{}`.

## Consequences

- A field change keeps the block; a re-read replaces it whole (labels and answers removed in the
  tracker disappear from it too).
- A project fed only by the poll keeps what the issue said when it was taken on: the poll does not
  re-read issues it already holds (one request per issue per pass would be the price).
- An answer's text is third-party: a template that interpolates it into a command, a path or a query
  is the author's bug; the platform only marks it.

## Alternatives discarded

- **Top-level keys in `fields` (`reporter`, `answers`…)**: they collide with a workflow's own inputs.
- **A new column or a contract change on `WorkItem`**: `fields` already reaches every reader (stage
  input, internal ticket, jobs, evals); a column would need each of them taught.
- **Answers keyed by `customfield_*` id**: meaningless to an agent and different on every site.
