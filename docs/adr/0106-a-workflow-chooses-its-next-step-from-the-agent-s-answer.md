# 0106 — A workflow chooses its next step from the agent's answer

- **Status**: accepted, 2026-10-28 (product owner: "the point of the platform is to decide, on an answer and a
  confidence, to go on to this step or that one"; the same is asked of development workflows: "develop it, then
  answer: is it finished? are there findings?")
- **Concerns**: `workflow.schema.json` (`transition.when`, `$defs/outputCondition`),
  `choregos_contracts.workflow` (`Transition.when`, `OutputComparison`), `choregos_core.dsl.conditions`,
  `WorkflowEngine.select_transition`, the DSL validator (`when.no_default`, `when.unknown_output`), the
  interpreter (`last_outputs`) and `load_context`; story S48-01

## Context

`select_transition` read the id of an incoming event (`ci_failed`, `changes_requested`, `merged`) and otherwise
took the first transition declared from the state. An agent's answer could stop a transition (a gate such as
`outputs_in` fails it) but never choose between two. A workflow that had to go one way on `outcome: pr` and
another on `outcome: reply` — the Fondation requests agent, ported from a Tekton pipeline on 2026-10-28 — had to
hand the choice to a person. A development step that should loop until the agent says it is finished could not
be written at all.

## Decision

1. **A transition may carry `when`**, a condition on the outputs of the work item's last agent or job stage:
   each key is an output name, each value a condition, and every condition must hold.
   - A value matches when equal: `when: { outcome: pr }`.
   - A list matches one of its values: `when: { outcome: [pr, reply] }`.
   - An object compares: `equals`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`:
     `when: { confidence: { gte: 0.8 } }`, `when: { findings: { gt: 0 } }`.
2. **Selection.** Among the transitions leaving a state, after an incoming event's transition (unchanged) and
   leaving out the repair roles (unchanged), the first in declaration order whose `when` holds is taken. A
   transition without `when` always holds: it is the default.
3. **Agents write JSON loosely, so comparisons forgive the form and not the absence.** A number written as a
   string compares as a number; `"true"`/`"false"` (and `yes`/`no`) compare as booleans; text compares trimmed and
   case-blind. A boolean is never a number. An output the stage did not write, or wrote as `null`, never matches:
   an agent that says nothing takes the default, never a branch it did not choose.
4. **Confidence is an output like any other.** The platform does not invent a confidence score; a workflow asks
   its agent for one (`outputs: [done, confidence]`) and compares it. Measured evidence stays with gates.
5. **The validator refuses** a `when` that names an output no transition of the workflow declares
   (`when.unknown_output`: a typo would never match), and a state whose exits all carry `when`
   (`when.no_default`: a work item whose answer matches none would wait forever).
6. **The interpreter keeps the last stage's outputs** (`last_outputs`), taken from the result the activity
   returned, so a replay reads the same values. They cross a `continue_as_new` in its payload, and a restarted
   interpreter reads them back from the database (`load_context`: the outputs of the work item's last finished
   run with a result). Human, system and train steps do not change them.

## Consequences

- A workflow loops on an agent's answer (`done: false` back to the step), escalates on a low confidence, and
  routes on a classification, with no person in between unless the workflow names one.
- Existing workflows have no `when`: they select exactly as before, and every archived history replays.
- A workflow that wants the choice made by a person still declares a human step; `when` makes it optional, not
  forbidden.
- Not decided here: conditions on the work item's fields or on gate outcomes (a gate already refuses a
  transition), and expressions combining outputs (`a > b`).
