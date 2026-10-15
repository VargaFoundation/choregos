# Concepts

Seven ideas carry the whole platform. Everything else is plumbing.

## Project

A project is a unit of work with its own connectors, workflow, policy and budgets. It lives
in an organisation. Its configuration (`ProjectConfig`) names the repository, the model
profiles and the labels; its **connectors** say which outside systems it talks to.

Connectors ship for: trackers (`github-issues`, `jira`, `gitlab-issues`, `internal`), source
control (`github`), CI (`tekton`), CD (`argocd`), execution (`tekton`, `k8s_job`, `aca`,
`local_docker`), model gateway (`litellm`, `direct`), memory (`ecphoria`, `lexical`) and
notification (`slack`). Each one has a `fake` twin used by the test suite.

`tracker: internal` means there is no outside tracker: Choregos' own database holds the
tickets, assigns their keys and discovers them. Use it for work that exists nowhere else —
an HR request, a file to process — or for a bench.

## Workflow

A YAML state machine, versioned, pinned to a ticket when it starts. States have a `kind`
(`wait` for a state that expects a human) and may be `terminal`. Transitions declare who
performs them (`by`), what they consume and produce (`inputs`, `outputs`), which guarantees
must hold (`gates`), and what happens when they fail (`on_fail`, with `max_attempts` and
`escalate_to`).

The DSL is validated statically. A workflow that cannot be satisfied is rejected **when it
is written**, not when a ticket is halfway through it: unreachable states, unbounded retry
loops, unknown gates, and gates that would have nothing to check.

## Artifact

What a step leaves for the next one (ADR 0049): one revision of a named output of a ticket — a
specification, a list of profiles, a SARIF report, a slide deck. Each revision has a type, a media
type, a size, the sha256 of its bytes and the run that produced it (stage, attempt, agent and
version). Markdown and JSON values stay in the platform's database; files go to the object store.
The digest is checked again on every read: content altered behind the platform is not served. The
same content produced again makes no new version.

A workflow also says, from its YAML alone, how documents travel between its steps (the artifact
flow, `graph.artifacts`): for each name, the transitions that output it, the ones that list it in
`inputs`, and where it comes from. `step`: some transition outputs it; `feedback`: `human_feedback`,
written by a person's rejection; `field`: a property of the work item's fields
(`metadata.inputs`); `missing`: read, but nothing produces it. It also marks the documents the
platform hands every agent stage (`spec_markdown`, `plan_markdown`, `allowed_paths`) and those the
pull request body carries. Only declared outputs and inputs count.

## Actor

Three kinds:

- **`agent`** — a role and a playbook, a model profile, and budgets in turns and minutes.
- **`human`** — a group, an SLA in hours, a reminder and an escalation.
- **`system`** — a transition the platform performs itself, with no agent and no cost.

## Agent scope

An agent is a registered, versioned object ([ADR 0033](adr/0033-agent-registry-and-skill-library.md))
with an **owner**, its scope ([ADR 0061](adr/0061-agent-ownership-scopes-and-forks.md)):

- **common** (`platform`) — installed from the built-in catalogue; only an organisation administrator
  changes it. To extend it, **fork** it;
- **organisation** (`org`) — shared by every project, written by the organisation;
- **project** — one project's; its project owner changes it, and it runs only in that project;
- **personal** — one person's drafts and experiments; nobody else sees it.

A **personal agent never runs in a workflow**: a process a whole team runs must not depend on an agent
one person can change at will or take away when they leave. It runs in trials, and is promoted to a
project (or forked into one) to reach a workflow. A **fork** is a new agent whose first version copies
one version of its source and which remembers where it came from; it never follows the source on its
own — it tells you when the source moved on.

## Stage and run

A transition performed by an agent becomes a **run**: one disposable sandbox, one workspace,
one short-lived token, one model key with a hard cap. The runner speaks
**ACP** (Agent Client Protocol, JSON-RPC over stdio) to the agent, records every message, applies the
guardrails (scope, forbidden commands, network, sensitive files), loops on the definition of
done, and publishes a `StageResult`.

Runs are idempotent: a replayed run does not pay twice and does not act twice.

## Gate

A gate is a **mechanism**, not an instruction. `evidence_present` requires tests that were
actually executed; `scope_respected` compares the diff to the declared scope;
`outputs_present` checks that the step produced what the transition declares;
`evidence_facts` reads facts the business named (`profils_retenus: 3`) and can require a
minimum. Gates shipped today:

```
action_succeeded · adr_number_free · ci_green · coverage_delta_min · diff_size_max
evidence_facts · evidence_present · external · flag_present · markdown_sections
no_secrets · outputs_in · outputs_present · provenance_signed · review_approved
scans_ok · scope_respected · tool_called
```

`adr_number_free` guards numbered records: when the branch adds `docs/adr/NNNN-*.md` (the path
pattern is the `pattern` parameter), it lists the records already on the default branch and refuses
a number that one of them carries — two studies running together both pick "the highest plus
one", and the second to merge is sent back to renumber. The refusal names the files in question.
It is evaluated when the merge transition starts, alongside the other synchronous gates.

**A gate that cannot see what it must check refuses.** If the SCM connector cannot produce a
diff, `scope_respected` does not pass — it declines. A guarantee that passes blindly is worse
than no guarantee, because it reassures.

## Evidence

What a step leaves behind, in the result: tests run and passed, lint, typing, coverage delta,
diff size — and `facts`, a flat map named by the business for work that has none of those.
Evidence is what gates read and what the UI shows; it is not prose.

A drafted agent is a proposal: a model can write a first version from a description, and a person
reads it, edits it and creates the agent.

## Tools

An agent's own tools are whatever its backend ships. The **platform's** tools reach it over
MCP on localhost: reporting a finding, asking a human, requesting a scope extension — and the
**catalogue**, a short, deliberately chosen list of third-party APIs and MCP servers that the
platform calls on the agent's behalf.

The agent never holds a provider credential, never chooses a URL, and never sees the remote
tool's real name. Your run token never reaches a third party. Each tool opens to groups, and
a project both declares what it uses and belongs to the groups that entitle it.

The list is written and reviewed like code. Choregos does not ask a remote server what it
offers: an inventory that updates itself is not an access control.

## Findings and trains

An agent that spots a problem **outside its scope** does not fix it: it reports a *finding*,
which is deduplicated, triaged and turned into a linked ticket. A *release train* batches
what is ready, opens its window, requires its approval, and can be frozen with a reason. A
project has trains only when one of its active workflows has a `via: release_train` transition
(`has_release_train` on the project): otherwise — an HR project born from `joiners-leavers`, say —
the console shows no trains tab and no "trains in flight". A
ticket labelled `hotfix` takes the *express lane*: the train leaves at once, with a shorter soak,
under the express lane's approval (`express_lane.approval`) instead of the ordinary one, and
still under the approval the ticket's own workflow requires (ADR 0041). A ticket keeps the labels
it was born with and follows its tracker when they are added or removed.
During the soak, each canary step and the final check, the train listens to Argo CD: a
`cd.app.degraded` or `cd.rollout.aborted` reported for its environment and one of its
applications rolls the departure back at once, without waiting for the next poll of the CD.
Polling stays the safety net; an event received while the train was collecting describes the
previous release and is dropped.

## Where the money goes

Every model call is minted against a per-run virtual key with a hard cap, and lands in a cost
ledger with its project, ticket, stage and model. Tool calls from the catalogue land in the
same ledger under `kind: tool`. Budgets are declared in the policy: per ticket, per stage, in
turns, in minutes, in tool calls.

A budget is a reservation ([ADR 0047](adr/0047-a-budget-is-a-reservation.md)). When the gateway
refuses a run's key because it is spent, the runner ends the run with `reason: budget`, and the
work item goes to `on_budget_exceeded` (else `on_question`, else the retry policy's `escalate_to`):
a person decides. A stage stopped on budget is **never retried** — a retry would mint a fresh key.

A run's key is minted at what is **left**: the smallest of the stage budget, the agent's run and day
budgets, the ticket budget and the project's day budget (`daily_project_usd`), each minus what the
cost ledger says was spent (models and tools) and minus the keys still open. Below
`CHOREGOS_MIN_RUN_RESERVATION_USD` (default 0.01 $) the stage does not start: no key, no run, and
the work item goes to a person the same way.

What a work item has spent is read from the cost ledger, models and tools alike, not kept in the
interpreter's memory: an interpreter started again for the same work item starts from what was
already spent.

A work item can be given its own budget, which only **tightens** the others: `budget_usd` when it
is created (API, MCP door, the board's form), or a workflow's `budget: {ticket_usd, on_exceeded}`
block. Wider than the workflow's or the policy's ticket budget is refused (422). `on_exceeded` names
where a work item of that workflow goes when its budget is spent.

The policy also bounds how long a work item may keep trying. `attempts.by_role` caps an agent
transition's `max_attempts` for the roles it names (it only lowers what the workflow says), and
`attempts.max_stages_per_ticket` (default 50) caps the agent stages a work item runs, retries
included: the next stage is refused and the work item goes to a person. `attempts.default_max` is
accepted but not enforced, and saying so is logged.

The project's day (`budgets.daily_project_usd`) is enforced twice. The reservation counts what the
project already spent today (and its open keys); and with a LiteLLM gateway the project has a
**team**, created at provisioning with that budget per day (`1d`), that every run key of the
project joins: the gateway itself refuses calls once the day is spent, whichever run makes them.

More money is a decision. "Replay the stage" on a work item whose budget is spent is refused (409)
unless it carries a top-up (`top_up_usd`, or `choregos items action <id> rerun_stage --top-up 5`);
the top-up adds to the work item's budget and the audit log records who granted it
(`workitem.budget_top_up`).
