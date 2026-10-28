# 0109 — The map edits a whole workflow: actors from the registry, every transition field, a workflow born on the map

- **Status**: accepted, 2026-10-28, by the product owner ("we must be able to edit workflows
  graphically")
- **Concerns**: `choregos_core/dsl/edition.py` (new typed operations), `POST /workflows/edit`, the
  console's map and process view (`components/workflows/`), `/p/<slug>/workflows/new`; amends
  [ADR 0023](0023-a-visual-workflow-builder.md) §3 and continues
  [ADR 0031](0031-several-workflows-per-project.md) §6; uses [ADR 0108](0108-a-workflow-catalogue.md)

## Context

ADR 0023 and ADR 0031 decided how a workflow is edited without leaving the YAML as the document: each
gesture is a typed operation grafted into the text, re-validated, and an operation followed by its
inverse gives back the original bytes. S16-11 and S16-12 delivered twelve operations and the map's
panels. Surveyed on `main` at `0a29b5b`, what a person can do **without touching YAML**:

- on a state: rename it (every reference follows), change its label and kind, remove it, add a step
  after it;
- on a transition: change who acts among the actors **already declared**, its timeout, add or remove a
  guarantee, remove it.

What still needs the YAML editor:

- **actors**: `add_actor`, `set_actor` and `remove_actor` exist in the API and nothing in the console
  calls them, so a person cannot add an agent or a human group to a workflow, nor pick an agent from
  the registry or a catalogue;
- most of a transition: `inputs`, `outputs`, `on_fail`, `on_reject`, `does`, a human `task`'s form
  (ADR 0035, S20-06), a governed `action`, `spawn` and joins (ADR 0057), a release train. The API's
  `set_transition` already writes any field as a flow value; the console has no form for them, and a
  `task` or an `action` written as one flow line is not a diff a reviewer reads;
- the workflow itself: its description, its initial state, its work-item fields (`metadata.inputs`),
  the `defaults` block;
- **creating a workflow**: "new workflow" publishes a template and opens the YAML page.

So "editable graphically" is true for the shape and false for most of the content.

## Decision

1. **The principle of ADR 0023 and 0031 stays**: the YAML is the document, every gesture is a typed
   operation (`POST /workflows/edit`), validated by `parse_workflow`, shown as a diff in the draft, and
   saved with `PUT` and its `base_version`. Each new operation ships with its inverse round-trip test on
   every core, template and demo workflow, a validator case, and a console test. No intent ships that a
   reviewer could not read as a diff.
2. **Actors from the registry.** The map gets an *Actors* panel built on the existing operations:
   - *add an agent*: a picker lists what ADR 0108 §2 resolves — the organisation's agents, installed
     plugins' agents, remote agents — with their role and version; choosing one writes
     `{type: agent, role: <its role>, agent: <slug>}` (pinned `@N` only on request). An agent that is not
     installed is shown with the gesture that installs it, never installed silently;
   - *add a human group*: a group the organisation knows (ADR 0044), with its SLA;
   - *add a job*: a job image the deployment declares (`CHOREGOS_JOB_IMAGES`), never a free reference;
   - change or remove an actor; removing one still used by a transition is refused by the validator, as
     in YAML.
3. **Every transition field has a form**, over the existing `set_transition`, with block style for
   nested values (`task`, `action`, `spawn`) so the diff stays readable. In this order: `inputs` and `outputs` (names from the actor's catalogue output contract, ADR 0040), `on_fail`,
   `on_reject`, `does`, `task` (a form builder over `TaskSpec`: title, instructions, fields as a JSON
   Schema object, attestation), `action`, `spawn`/join, `via: release_train`. A new
   `set_workflow` operation covers `metadata.description`, `metadata.inputs` (the work-item fields, with
   the same field builder) and `initial`.
4. **Drawing a transition is a gesture, not free wiring** — this **amends ADR 0023 §3**. Dragging from
   one state to another opens the add-transition form with both ends filled; nothing is written until
   the form is confirmed, and what is written is the `add_transition` operation. Node positions are
   still computed, never saved: there is no layout to drift from the YAML.
5. **A workflow can be born on the map.** "New workflow" starts from a catalogue entry (ADR 0108) or
   from a **blank** skeleton — one human actor, `inbox` → `done` — that the validator accepts, and opens
   the map, not the YAML page. The YAML editor stays one tab away and is never the only way.
6. **Every gesture has a keyboard and form equivalent**, as the map's existing panels do (S16-12): the
   drag of decision 4 is a convenience over the "add a step" form, not the only path.

## Consequences

- A person who does not read YAML can build `default-request` (ADR 0107) or an HR workflow from a blank
  page, choosing agents from the registry, and what lands in review is a readable YAML diff.
- The operation set roughly doubles; the round-trip test over every shipped workflow is what keeps the
  text grafts honest as it grows.
- Stories S49-09 to S49-13 in `docs/plan/08-backlog.yaml`.

## Alternatives discarded

- **A graph model saved next to the YAML** (positions, groups) — refused by ADR 0023 and 0031 for the
  same reasons: two sources of truth and a second validator.
- **Adopting a third-party builder** (React Flow editors that generate YAML, n8n-style). They own the
  model and generate the YAML, the wrong way round for a document that is reviewed and replayed.
- **Free drag-to-connect with no form.** A transition without its actor, guarantees and failure path is
  either refused by the validator or written with defaults nobody chose.
