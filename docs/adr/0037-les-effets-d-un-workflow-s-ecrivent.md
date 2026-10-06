# 0037 — What a workflow does is written, not carried by a state's name

- **Status**: accepted, 2026-10-06
- **Concerns**: the workflow DSL (`Transition.does`, `State.production`), the validator, the typed
  edits, the interpreter; follows [ADR 0023](0023-editer-un-workflow-depuis-la-console.md) and
  [ADR 0031](0031-plusieurs-workflows-par-projet.md) (typed edits); closes issue #175

## Context

Three behaviours of the platform were carried by the **name** of a state: a system transition to a
`pr_*` state opened the ticket's pull request, one to a `merged*` state merged it, and a
`deployed_prod*` state was production — reachable only through a release train. Since ADR 0031 a
state can be renamed from the console. Renaming `pr_open` to `review` silently stopped opening the
pull request; renaming `deployed_prod` lifted the production lock. The typed edit only warned.

## Decision

1. **The DSL says it.** `does: open_pr | merge_pr` on a system transition is what the platform does
   when it takes it; `production: true` on a state makes it production (release train only). `does`
   on a transition whose actor is not `system` is refused.
2. **Names are still read, for what was written before.** With no `does`, the effect is inferred
   from the target state's name as before, and a state named `deployed_prod*` is still production.
   A published workflow keeps its behaviour, and a recorded Temporal history replays identically —
   the interpreter decides from the same definition, with the same result. The validator warns
   (`workflow.effet_implicite`) on every inferred effect.
3. **A rename writes the effect down first.** Renaming a state whose name carried an effect expands
   into `set_transition does` on the system transitions that reach it and `set_state production`,
   then the rename; the inverse undoes all of it, byte for byte. A transition with no `id` cannot be
   targeted: that rename is refused rather than done silently.
4. **The templates are explicit.** The built-in templates were rewritten by the typed edits
   themselves; the template conformance suite refuses a warning, so a template with an inferred
   effect cannot ship.

## Consequences

- The process view says it in plain words ("the platform, which opens the pull request"), and the
  connector requirements read the effect, not the name (`scm` for a transition that opens or
  merges, `cd` for a production state).
- A future effect of the platform is a new value of `does`, not a new magic prefix.
