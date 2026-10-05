# 0031 — A project runs several workflows; each work item is pinned to the version it was born in

- **Status**: accepted, 2026-10-05
- **Concerns**: `workflow_defs`, `work_items.workflow_def_id`, the interpreter's `load_context`, the
  work-item constructors, the tracker webhooks, templates, the console's workflow pages; amends
  [ADR 0023](0023-constructeur-visuel-de-workflow.md); follows [ADR 0029](0029-une-plateforme-d-agents-gouvernes.md)

## Context

A human-resources project needs two workflows side by side — onboarding and offboarding. Choregos
could not hold them:

- `active_workflow()` returns the highest active version **whatever its name**, and a second
  `PUT` deactivates the first;
- `work_items.workflow_def_id` exists and is never written: the interpreter, the tracker mirror
  and the reconciliation read the project's workflow, not the item's;
- `migrate` sends an id where the interpreter expects a definition;
- five code paths build a work item, and two of them write the state `inbox` by hand, so an item
  dies at birth on any workflow that starts elsewhere.

The specification has the same gap: every route and requirement is singular, while its own
templates ship several flows.

## Decision

1. **Several workflows per project, one active version per name.** A partial unique index
   `(project_id, name) WHERE is_active` holds it. The project names its **default** workflow
   (`projects.default_workflow`); `/projects/{id}/workflow` stays as the default's alias.
2. **A work item is bound at birth**, in this order: an explicit `workflow`; else the first
   matching routing rule of the project (`labels_any`, `labels_all`, `item_type`); else the
   default. The item records the **version** (`workflow_def_id`) and keeps it: publishing a new
   version never moves a running item. `migrate` moves it on purpose.
3. **One constructor.** Every path that creates a work item goes through `nouveau_ticket`, which
   sets the initial state of the chosen workflow and the pin together; a test refuses any other
   construction.
4. **Replay safety.** Only the body of the `load_context` activity changes: it reads the pin, and
   writes it when it is missing (idempotently). `InterpreterInput` does not change, so a worker
   from before the change still runs. The `migrate` fix goes under
   `workflow.patched("migration-par-definition")`, with a new recorded history.
5. **Work items carry fields.** A workflow declares the JSON Schema of its inputs
   (`metadata.inputs`); `work_items.fields` holds the values, validated at creation, available to
   agents and, later, to date-based transitions.
6. **Readable and editable.** The console lists the workflows of a project, shows each one as a
   plain-language process and as a map, and edits it through typed operations
   (`POST /workflows/edit`). **This amends ADR 0023**: no round-tripping library is in the lock, and
   `ruamel.yaml` would restyle the file; the operations are **text grafts** guided by the node
   positions `yaml.compose` gives, re-validated by `parse_workflow`, and an operation followed by
   its inverse gives back the original bytes. YAML anchors are refused.

## Consequences

- Backlog stream S16: the pin, routes by name, birth in the right workflow, fields, `migrate`, the
  CLI, multi-workflow templates, the process view, the workflows tab, a board per workflow, typed
  edits, editing from the map, version history.
- **Deployment order**: the workers of the pin must run before a second workflow is published,
  or an old worker would run the highest version whatever its name.
- State names with a hidden effect (`pr_*`, `merged*`, `deployed_prod*`) keep it until the system
  actor's effects become explicit (issue #175); renaming such a state warns.

## Alternatives discarded

- **An `is_default` flag on each version.** The default belongs to the project, not to a version;
  a flag would have to be copied from version to version.
- **Routing rules inside `ProjectConfig`.** `PATCH /projects/{id}` replaces the whole
  configuration and would erase them.
- **A graph model edited by the console**, as other builders do. A second model needs a second
  validator; the YAML stays the document (ADR 0023).
