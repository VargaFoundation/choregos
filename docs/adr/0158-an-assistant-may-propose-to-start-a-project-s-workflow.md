# 0158 — An assistant may propose to start a workflow a project already runs

- **Status**: proposed, 2026-11-12 (the amendment it records was approved by the product owner the same day)
- **Concerns**: `packages/contracts/schemas/assistant.schema.json` (a new key `launches`),
  `choregos_core.plugins.assistants` (validation), a new table `assistant_launch_bindings`, two columns of
  `conversation_actions` (`kind`, `target`), `services/assistants.py` (binding, readiness, upgrade),
  `services/conversation_actions.py` (proposal, confirmation), the route
  `PUT /orgs/{org}/assistants/{slug}/launches/{launch}`, the console's installation page and action card;
  amends [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md) (an
  assistant declares launches beside its tools); builds on
  [ADR 0035](0035-governed-actions-in-the-core.md), [ADR 0061](0061-agent-ownership-scopes-and-forks.md),
  [ADR 0129](0129-an-artifact-says-how-it-is-shown.md),
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md),
  [ADR 0137](0137-a-standing-read-only-delegation.md) and
  [ADR 0139](0139-an-assistant-through-the-mcp-door-and-an-embedded-component.md); stream S66

## Context

An assistant can start only its own **tasks** (ADR 0137): a workflow file of the same plugin, run with
`tracker: internal` in the person's personal space. An organisation that already runs a workflow in one of
its projects — say a project whose prompt and sandbox agents write a document from a request — cannot let an
assistant start it. It would have to declare the workflow again in the plugin, with its agents, its
connectors and its budget: a duplicate that drifts from the project's own, run outside the project that owns
the process, its people and its spend.

What the person wants is narrower: from a conversation, *"start that for me"*, with the inputs the
conversation already holds, in the project that runs it, and later *"where is it?"*.

## Decision

1. **An assistant declares launches.** The manifest gains `launches: [{name, description, input_schema,
   required}]`: a workflow the assistant may *propose* to start. `name` is English `snake_case` and unique
   among launches; `description` is what the agent reads to decide when to propose it; `input_schema` (a JSON
   Schema of `type: object`) is what the assistant fills from the conversation; `required` defaults to
   `true`. The manifest names no project and no workflow: those are the organisation's, and the plugin's
   author cannot know them. A need may not be called `launch` in an assistant that declares launches (its
   tools would collide with the launch tools of point 4). Checked by the schema, then by `choregos plugin
   validate`.

2. **A launch is bound to an existing project and workflow, by reference.** `assistant_launch_bindings`
   (installation, launch, project, workflow name, `enabled`, who bound it) under the organisation's forced
   row-level security; `PUT /orgs/{org}/assistants/{slug}/launches/{launch} {project, workflow, enabled}`.
   It is done at install, on the installation page, and later by the installation's owners, as tool needs
   are (ADR 0133 point 3): the install request itself carries no binding. Binding needs **both**
   `assistant:manage` on the installation **and** `item:control` in the bound project — the right to create
   work items there (ADR 0061's matrix, a developer and above): an owner of an assistant cannot open, through
   it, a project they could not file work in. The binding is refused (422) when the project is a personal
   space (ADR 0134), when it has no active workflow of that name, or when the workflow's `metadata.inputs`
   cannot accept what the launch fills: a required input the launch's `input_schema` does not declare, or a
   declared property that the workflow's closed inputs refuse. The reference is a name: the work item is
   born under the project's **active version** of that workflow at confirmation, like any work item filed
   there; nothing is copied.

3. **Readiness.** A required launch that is not bound, or switched off, makes the installation `incomplete`
   (`launch_unbound`). A bound workflow that the project no longer has active (`launch_workflow_missing`)
   or whose inputs no longer accept what the launch fills (`launch_inputs_mismatch`) is an item too, for the
   owners to fix. A project deleted takes its bindings with it (the launch is then unbound). An optional
   launch may be switched off. A plugin upgrade follows the need-change path of S63-22: a launch the
   manifest dropped has its binding switched off and kept; a launch whose `input_schema` changed is checked
   again against its workflow and switched off, with a readiness item, when it no longer fits; a new launch
   is unbound and listed; the owners are told by the same notice.

4. **In a conversation, the assistant proposes; it never starts.** Each bound, enabled launch is offered as
   a tool `launch__<name>(title, inputs)`; `inputs` is the launch's `input_schema`. Calling it starts
   NOTHING: arguments the schema refuses are answered to the agent; a person who may not create work items
   in the bound project is told so and nothing is stored; otherwise a `ConversationAction` of
   `kind: launch` is stored `proposed`, with the arguments, a readable summary and its `target` (the project
   and the workflow at the time of the proposal). It is the pending action of ADR 0139 points 3-5 and S63-10:
   listed by `GET /me/conversations/{id}/actions`, carried by the door's `pending_actions[{id, summary,
   confirm_url}]`, expired after a day, decided by the conversation's owner in the console or the embedded
   component. `mcp:read` stays enough to converse; the door decides nothing and executes nothing. There is
   no `console` approval for a launch: the bound workflow carries its own gates, and the person confirming
   is the one whose rights are used.

5. **Confirming creates the work item as the person.** The confirmation re-reads the binding — it must
   still be enabled and point at the proposal's project and workflow, else `409 launch_rebound` and nothing
   is created — and the **confirming person's** rights from their own request: without `item:control` in
   the project, `403 launch_forbidden` and nothing is created. The work item is then created through the one
   creation path (`tickets.create_work_item`, the REST route's and the MCP door's), the person as principal:
   they are its reporter and its initiator (`person:<email>`, ADR 0077), the bound workflow, `inputs` as its
   fields — validated by the workflow's `metadata.inputs` (422, nothing created) — and a body that names the
   installation and the conversation. The project's tracker applies, an external one included. The action
   becomes `executed`, its `result` naming the work item (`id`, `key`, project, workflow and its console
   address); the conversation is told, journaled (`action.confirmed`) and audited
   (`assistant.launch.confirm`). Refusing is the existing refusal.

6. **The conversation can follow what it started.** When the installation has a bound launch, the agent is
   offered `launch_status(action_id?)`: for each launch the conversation started, the work item's key, title
   and state, whether it ended, and its artefacts (ADR 0129: name, version, type, how it is shown and the
   address of its content), read with the person's current rights — a work item they can no longer read is
   reported as such, without its content. That is the minimum. **Left out**: a message pushed into the
   conversation when the work item moves or ends (the person or the agent asks); an artefact's content in
   the conversation (the agent gives its address); following a work item the conversation did not start;
   launches for `hosted` agents, which inline writes do not reach either.

7. **Budget and audit are the project's.** The work item is charged, limited and journaled like any work
   item of its project: its policy, its budgets, its ledger, its approvals. The installation's caps and the
   person's assistant month count the conversation's turns only. Every call of `launch__<name>` and of
   `launch_status` writes a line of the reads journal (ADR 0133 point 6) with the launch as its need, the
   operation (`propose`, `status`) and, once there is one, the work item's key; the action row keeps the
   link between the conversation and the work item.

8. **No business vocabulary in the core** (ADR 0133 point 7): the core knows launches, projects, workflows
   and inputs, never what the workflow does.

## Alternatives rejected

- **Declaring the workflow again in the plugin** (a task): the duplicate the amendment exists to avoid, run
  outside the project that owns the process, its agents and its spend.
- **A generic `start_workflow(project, workflow)` tool** open to any project the person may write in: the
  installation would no longer decide what the assistant offers, and a prompt injection could start any
  process the person can.
- **Starting the workflow from the tool call, with `mcp:write` or the service's identity**: contradicts ADR
  0139 point 4 (a conversation reads, its writes are proposals) and would file work under someone who did
  not confirm it.
- **Running the work item in the person's personal space**: the project's agents, connectors, budget and
  people are in the project.
- **Binding by the plugin's author** (a project slug in the manifest): an organisation's projects are not
  the plugin's to know, and a slug in a public manifest is a guess.

## Consequences

- A contract change: `launches` in `assistant.schema.json`; in `openapi.yaml`, the binding route,
  `Assistant.launches`, the readiness kinds and `launch`, and `ConversationAction.kind` and `target`.
- A migration: the table `assistant_launch_bindings` and `conversation_actions.kind` and `.target`.
- The console's installation page binds launches; its action card says what a launch would start and, once
  executed, links the work item.
- The person who confirms needs a role in the bound project. An organisation that wants a wide audience to
  start a project's workflow gives that audience the developer role there, or keeps the workflow's own gates
  for what must be reviewed.
