---
title: Workflow YAML
description: Every key of a Choregos workflow, with its type, its default and what it does, and complete examples that the platform's own parser checks.
sidebar_position: 2
---

# Workflow YAML

A workflow is a state machine: **states** a work item can be in, **actors** who move it (agents,
people, the system, jobs, decision models), and **transitions** that say who moves it from where to
where, under which **gates**. This page lists every key. Both examples below are parsed by the
platform's own validator in the repository's tests, and the list of keys is checked against the
workflow schema: a key added to the schema without this page turns the build red.

Check a file before publishing it:

```bash
choregos workflow validate ship-a-change.yaml
choregos workflow show ship-a-change.yaml --process
```

## A complete example

The `ship-a-change` workflow of a development project: an agent refines the request, the product
owner approves the specification, an agent implements it under gates, another verifies, the platform
opens and merges the pull request, and a release train ships it.

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: ship-a-change
  version: 1
  description: Refinement, approval, implementation, verification, pull request, merge, release.
  inputs:
    type: object
    required: [ticket]
    properties:
      ticket: { type: string }
      priority: { type: string, enum: [low, high] }
actors:
  refiner: { type: agent, role: refine, model: "profile:standard" }
  owner: { type: human, group: product-owners, sla_hours: 24 }
  dev: { type: agent, role: implement, model: "profile:by_size" }
  checker: { type: agent, role: verify, model: "profile:standard" }
  ci: { type: system }
  captain: { type: human, group: release-captains, sla_hours: 4 }
states:
  inbox: { display: To triage, kind: wait, tracker: { status: Todo, label: "choregos:inbox" } }
  refining: { display: Refining, tracker: { status: Refining } }
  awaiting_spec_approval: { display: Spec to approve, kind: wait, tracker: { status: Needs review } }
  ready: { display: Ready, tracker: { status: Ready } }
  in_progress: { display: In progress, tracker: { status: In progress } }
  verifying: { display: Verifying, tracker: { status: Verifying } }
  pr_open: { display: PR open, kind: wait, tracker: { status: In review } }
  merged: { display: Merged, tracker: { status: Merged } }
  deployed_prod: { display: In production, terminal: true, production: true, tracker: { status: Done } }
  needs_human: { display: Needs a human, kind: wait, tracker: { status: Blocked } }
  abandoned: { display: Abandoned, terminal: true, outcome: cancelled, tracker: { status: Cancelled } }
transitions:
  - id: t-refine
    from: inbox
    to: awaiting_spec_approval
    by: refiner
    outputs: [spec_markdown, size, risk]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-approve-spec
    from: awaiting_spec_approval
    to: ready
    by: owner
    on_reject: refining
    timeout_hours: 72
  - id: t-refine-again
    from: refining
    to: awaiting_spec_approval
    by: refiner
    on_fail: { to: refining, max_attempts: 2, escalate_to: needs_human }
  - id: t-implement
    from: ready
    to: in_progress
    by: dev
    inputs: [spec_markdown, allowed_paths]
    gates: [scope_respected, { name: diff_size_max, params: { files: 60 } }, no_secrets]
    on_fail: { to: ready, max_attempts: 2, escalate_to: needs_human }
  - id: t-verify
    from: in_progress
    to: verifying
    by: checker
    inputs: [spec_markdown]
    gates: [evidence_present]
    on_fail: { to: in_progress, max_attempts: 2, escalate_to: needs_human }
  - id: t-open-pr
    from: verifying
    to: pr_open
    by: ci
    does: open_pr
    pull_request: { draft: false }
    gates: [evidence_present]
    on_fail: { to: verifying, max_attempts: 2, escalate_to: needs_human }
  - id: t-merge
    from: pr_open
    to: merged
    by: ci
    does: merge_pr
    gates: [ci_green, review_approved, scans_ok]
    timeout_hours: 48
    on_fail: { to: in_progress, max_attempts: 3, escalate_to: needs_human }
  - id: t-deploy
    from: merged
    to: deployed_prod
    via: release_train
    train: { env: prod, approval: captain }
    timeout_hours: 72
  - id: t-abandon
    from: needs_human
    to: abandoned
    by: owner
    timeout_hours: 168
artifacts:
  diff: { show: facts, title: Changes }
budget:
  ticket_usd: 6
  on_exceeded: needs_human
a2a:
  skill: ship-a-change
  description: Ship a change through review and release.
  input_modes: [text/plain, application/json]
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
  needs_human:
    on_answer: resume
    on_abandon: abandoned
```

## Top level

| Key | Required | What it is |
| --- | --- | --- |
| `apiVersion` | yes | always `choregos/v1` |
| `kind` | yes | always `Workflow` |
| `metadata` | yes | the workflow's name, version and inputs ([below](#metadata)) |
| `actors` | yes | who moves work items, by name ([below](#actors)) |
| `states` | yes | where a work item can be, by name ([below](#states)) |
| `transitions` | yes | who moves it from where to where ([below](#transitions)) |
| `defaults` | no | what happens on a question, a spent budget, a timeout ([below](#defaults)) |
| `budget` | no | what a work item may spend ([below](#budget)) |
| `artifacts` | no | how each output is typed and shown ([below](#artifacts)) |
| `a2a` | no | serve the workflow as an A2A skill ([below](#a2a)) |
| `initial` | no | the state a work item starts in; the first declared state when absent |

Names of states, actors and transition ids are lower-case letters, digits, `-` and `_`, 63 characters
at most.

## `metadata`

| Key | What it is |
| --- | --- |
| `name` | the workflow's name in the project: lower-case letters, digits and `-` |
| `version` | an integer from 1 |
| `description` | one sentence |
| `extends` | `template:<name>@<version>`: start from a shipped template |
| `inputs` | the JSON Schema of a work item's fields; every door (console, CLI, REST, MCP, A2A, triggers) checks fields against it |
| `catalogue` | how the workflow catalogue presents it: `title` (80 characters), `summary` (400), `tags` (12 at most); the engine ignores it |

## `actors`

Each actor has a `type`.

| `type` | Keys | What it is |
| --- | --- | --- |
| `agent` | `role` (required), `agent`, `model`, `backend`, `playbook`, `max_turns`, `max_minutes`, `fresh_context` | an agent run in a sandbox. `agent` names a registry agent (`slug` or `slug@version`) whose version sets the instructions, model, limits and budget ([Build an agent](../build/agents.md)). `model` is `profile:<name>`, `profile:by_size` (the default, by the work item's size) or a model id. `backend` is the coding agent, the project's default otherwise. `playbook` names the instructions when there is no registry agent (the role's own otherwise). `fresh_context: true` starts without the previous runs' context |
| `human` | `group`, `user` (at least one), `sla_hours`, `escalate_to` | a person decides in the console: a member of the identity provider's `group`, or the person `user` names by e-mail. With both, that person is asked first and the group may answer once `sla_hours` (24 by default) has passed. An actor that approves a `train` names a `group` |
| `system` | none | the platform itself: opens or merges a pull request (`does`), proposes an action |
| `job` | `role` and `command` (required), `image`, `max_minutes` | a command run in the step's sandbox, without a model: `command` is an argument list, never a shell line; `image` is a job image the operator named. The facts it writes in `.choregos/facts.json` are measured |
| `decision` | `role`, `questions`, `state` (required), `model`, `timeout_seconds` | a decision model answers typed questions about the work item, without a sandbox. `state` lists what it reads (`title`, `description`, `labels`, `type`, `fields.<name>`, `outputs.<name>`). Each question writes an output `<name>` and `<name>_confidence`, which `when` reads |

A decision question has a `type`, `instructions` (what is asked, 2,000 characters) and, by type:
`yes_no` (nothing more), `choice` with `options` (2 to 100), `score` with ordered `levels` (2 to 10,
lowest first).

The roles of the shipped playbooks (`triage`, `refine`, `plan`, `implement`, `verify`, `review`,
`fix_ci`, `address_review`, `release_notes`, `verify_prod`, `custom`) keep their meaning; any other
name is yours.

## `states`

| Key | Default | What it is |
| --- | --- | --- |
| `display` | required | the name people read |
| `kind` | `work` | `work` (an actor is acting), `wait` (waiting for a person or an event) or `terminal` |
| `terminal` | `false` | the work item ends here |
| `outcome` | `success` | how a terminal state ends it: `success`, `declined` or `cancelled` |
| `production` | `false` | a production state, reachable only `via: release_train` |
| `tracker` | | how an external tracker shows it: `status` and `label` |

## `transitions`

A transition has `from` and `to`, and either `by` (an actor) or `via: release_train` with `train`.

| Key | What it is |
| --- | --- |
| `id` | a stable name for the transition; the console and the journal use it |
| `from` | a state, or `*agent` for any state an agent drives |
| `to` | the state it leads to |
| `by` | the actor that performs it |
| `via` | `release_train`: the work item ships with the next departure of a release train |
| `train` | with `via`: `env` (required), `approval` (a human actor who approves the departure), `auto_sync` |
| `gates` | the checks that must pass before the work item moves ([gates](#gates)): a name, or `name` and `params` |
| `inputs` | the documents and files the step receives |
| `outputs` | the documents the step must produce |
| `output_types` | the type of each output: `markdown`, `json`, `mermaid`, `sarif`, `text`, `csv`, `html`, `pdf`, `docx`, `xlsx`, `pptx`, `png`, `jpeg`, `gif`, `webp`, `svg`, `mp4`, `webm`, `file`, `diff`, `level`, `metric`, `keyvalue` |
| `when` | take this transition only when the last step's outputs match ([below](#when)) |
| `on_fail` | what happens when the step or a gate fails: `to`, `max_attempts`, `escalate_to` (all three required) |
| `on_reject` | for a human step: the state a rejection sends the work item to |
| `on_changes_requested` | for a review: like `on_fail`, when changes are requested |
| `review` | who reviews: `agents` (actor names), `humans` (`group`, and `required`: `always`, `by_policy` or `never`) |
| `timeout_hours` | how long the step may wait |
| `does` | what the platform does on a system transition: `open_pr` or `merge_pr` |
| `pull_request` | with `does: open_pr`: `draft: true` opens it as a draft |
| `task` | a form a person fills: `form` (a JSON Schema whose properties become fields), `title`, `instructions`, `attest` (what the person attests, word for word) |
| `action` | a governed action the system proposes ([below](#action)) |
| `spawn` | a fan-out: one child work item per element of a list ([below](#spawn)) |

### `when`

Every key is an output name of the last step; every condition must hold. Among the transitions
leaving a state, the first whose `when` holds is taken, and one without `when` is the default. A
condition is a value (equal), a list (one of), or an object of `equals`, `in`, `not_in`, `gt`, `gte`,
`lt`, `lte`. A missing output never matches.

### `action`

`kind`, `title` and `effects` are required. Each effect has an `effect` (an operation of a
connector), `with` (its arguments) and `compensate` (how to undo it). `justification` and `params` are
rendered with the work item's fields; `not_before` delays it to a date taken from a field
(`fields.start_date - 10d`). `approval` asks people first: `approvers` (each a `role`, a `group`, and
`min` people), `step_up_minutes` (they must have signed in within that time), `separation_of_duties`
(the requester cannot approve).

### `spawn`

`workflow` (the children's, in the same project), `for_each` (the list walked, such as
`fields.symbols` or `outputs.candidates`) and `title` are required; `fields` renders each child's
fields; `max_children` (20 by default), `succeeded` (the children's states that count as a success)
and `min_succeeded` decide when the transition passes.

## `defaults`

| Key | What it is |
| --- | --- |
| `from_any_agent_state.on_question` | where a work item goes when an agent asks a question |
| `from_any_agent_state.on_budget_exceeded` | where it goes when its budget is spent |
| `from_any_agent_state.on_timeout` | where it goes when a step runs out of time |
| `needs_human.on_answer` | `resume`: once a person answers, the work item goes back where it was |
| `needs_human.on_abandon` | where it goes when the person abandons it |

## `budget`

`ticket_usd` is what one work item may spend, which can only tighten the project's policy;
`on_exceeded` is the state a work item goes to when it is spent.

## `artifacts`

How each output is shown, by output name: `type` (`level`, `metric` or `keyvalue`), `title`, `show`
(`facts` on the work item's *About* card, `tab` by default, `hidden`), and for a scale either `scale`
(`risk`, `size`, `verdict`) or `levels` (each a `value`, a `title` and a `tone`: `neutral`, `info`,
`ok`, `warn`, `danger`), with `unit`, `min`, `max` and `better` (`higher` or `lower`) for a metric.

## `a2a`

Serves the workflow as a skill of the A2A door: `skill` and `description` (required), `input_modes`
(`[text/plain]` by default). See [Start a workflow over A2A](../integrate/a2a.md).

## Gates

A gate checks; it does not trust what the agent says. The synchronous ones rule at once from the step's
result and the diff; the others wait for an event.

| Gate | Checks |
| --- | --- |
| `scope_respected` | the change stays inside the allowed paths and out of the denied ones |
| `diff_size_max` | the change touches at most `params.files` files |
| `no_secrets` | the change adds no secret |
| `evidence_present` | the step brought its evidence (tests that ran, coverage) |
| `evidence_facts` | the facts a trade named are there, and comparable if asked |
| `outputs_present` | the declared outputs exist and are not empty |
| `outputs_in` | named outputs take an allowed value: `params.values` |
| `output_schema` | named outputs match a JSON Schema: `params.schemas` |
| `numbers_measured` | every number an agent writes comes from a measured document |
| `tool_called` | the tools the step required were really called, read from the ledger |
| `coverage_delta_min` | test coverage does not drop below the threshold |
| `docs_updated` | the branch updates the documentation when it changes anything else |
| `markdown_sections` | the documents the branch adds carry their required sections |
| `adr_number_free` | the number of an ADR the branch adds is not already taken |
| `already_fixed_proven` | the fix is already on the default branch, on measured evidence |
| `ci_green` | the pull request's CI passed (waits) |
| `review_approved` | the pull request was approved (waits) |
| `scans_ok` | the security scans passed (waits) |
| `provenance_signed` | the build's provenance is signed (waits) |
| `flag_present` | a high-risk work item names a feature flag that exists (waits) |
| `action_succeeded` | the transition's governed action succeeded (waits) |
| `external` | an external system reported its verdict (waits) |

## A second example: decisions, a job, conditions

A decision model sorts incoming bugs, a job reproduces the reproducible ones, the rest goes to a lead:

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: bug-triage
  version: 1
  description: A decision model sorts incoming bugs; a job reproduces them; a person takes the rest.
  inputs:
    type: object
    properties:
      ticket: { type: string }
  catalogue:
    title: Bug triage
    summary: Sort incoming bugs and reproduce the ones that can be.
    tags: [software, triage]
actors:
  sorter:
    type: decision
    role: triage
    state: [title, description, labels]
    questions:
      kind: { type: choice, instructions: "Is this a bug, a feature request or a question?", options: [bug, feature, question] }
      reproducible: { type: yes_no, instructions: "Does the report give steps to reproduce?" }
  reproducer: { type: job, role: reproduce, command: ["make", "reproduce"], max_minutes: 15 }
  lead: { type: human, group: developers, sla_hours: 8 }
states:
  inbox: { display: New, kind: wait }
  sorted: { display: Sorted }
  reproduced: { display: Reproduced, kind: wait }
  with_lead: { display: With the lead, kind: wait }
  closed: { display: Closed, terminal: true }
  declined: { display: Not a bug, terminal: true, outcome: declined }
transitions:
  - id: t-sort
    from: inbox
    to: sorted
    by: sorter
  - id: t-reproduce
    from: sorted
    to: reproduced
    by: reproducer
    when: { kind: bug, reproducible: true, reproducible_confidence: { gte: 0.8 } }
    outputs: [reproduction]
    gates: [outputs_present]
    on_fail: { to: sorted, max_attempts: 1, escalate_to: with_lead }
  - id: t-not-a-bug
    from: sorted
    to: declined
    by: sorter
    when: { kind: { in: [feature, question] } }
  - id: t-to-lead
    from: sorted
    to: with_lead
    by: sorter
  - id: t-close
    from: reproduced
    to: closed
    by: lead
  - id: t-close-by-hand
    from: with_lead
    to: closed
    by: lead
artifacts:
  reproduction: { show: tab, title: Reproduction }
budget:
  ticket_usd: 2
  on_exceeded: with_lead
```

## When the validator refuses

Each problem has a code, a line and a column. The most frequent:

| Code | Means |
| --- | --- |
| `schema.json` | a key the schema does not know, or a value of the wrong type |
| `yaml.syntax` | the file is not YAML |
| `state.unknown`, `actor.unknown`, `gate.unknown` | a name that is not declared (or not a known gate) |
| `state.orphan`, `state.no_inbound` | a state nothing leads to |
| `workflow.no_terminal_state`, `workflow.terminal_unreachable` | the work item could never end |
| `transition.unbounded_retry` | an agent or job step that goes back to an earlier state without `on_fail.max_attempts` |
| `transition.does_requires_system` | `does` on a transition whose actor is not `system` |
| `workflow.no_verify_before_pr` | a pull request opened without a verification step (a warning) |

The complete list is in [Error codes](errors.md#workflow-validation).
