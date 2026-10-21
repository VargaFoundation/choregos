# Concepts

Seven ideas carry the whole platform. Everything else is plumbing.

## Project

A project is a unit of work with its own connectors, workflow, policy and budgets. It lives
in an organisation. Its configuration (`ProjectConfig`) names the repository, the model
profiles and the labels; its **connectors** say which outside systems it talks to.
Its **readiness** (`GET /projects/{id}/readiness`) lists, from stored state, what it still lacks to run and who can fix each item.

Connectors ship for: trackers (`github-issues`, `jira`, `gitlab-issues`, `internal`), source
control (`github`), CI (`tekton`), CD (`argocd`), execution (`agent_sandbox`, the deployment's default; `tekton`, `k8s_job`, `aca`,
`local_docker`, each chosen by name), model gateway (`litellm`, `direct`), memory (`ecphoria`, `lexical`) and
notification (`slack`). Each one has a `fake` twin used by the test suite.

A **catalogue** is what the platform *offers*; a **connector** is what an organisation has
*installed* (the console shows them under *Installed connectors*); a **connector type** is one entry of the connector catalogue, the kind of system a connector can be an instance of (a directory, a tracker, a CI). Each project uses only what is enabled for it (ADR 0074). The image ships a catalogue of well-known MCP servers (and one of skills): listing an
entry enables nothing, and installing one goes through the same governance as declaring it by hand
([ADR 0065](adr/0065-catalogues-of-mcp-servers-and-skills.md)).

`tracker: internal` means there is no outside tracker: Choregos' own database holds the
tickets, assigns their keys and discovers them. Use it for work that exists nowhere else —
an HR request, a file to process — or for a bench. A work item filed from the console or MCP on
a project with an external tracker is created in that tracker first, then mirrored (ADR 0059).

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

- **`agent`** — a role and a playbook, a model profile, and budgets in turns and minutes. A
  registry agent version has a **runtime**: `acp` (the default — a coding agent the runner ships,
  chosen by `backend`) or `code` (an ACP agent written in code, in an image the operator named and
  built from the runner image, [ADR 0063](adr/0063-agents-written-in-code.md)).
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
own — it tells you when the source moved on. An agent also carries **tags**, labels people find it by;
they are metadata outside its versions, so editing them publishes nothing. A version carries a
**message**, and going back to an older one **appends** a copy of it (ADR 0076): nothing is rewritten.

## Agent kinds and runtimes

An agent of the registry has a **kind**: `internal` (the platform runs it), `external` (a client of the
MCP door, ADR 0033) or `remote` (the platform calls it over A2A, ADR 0062 — for example Atlassian's
Rovo). A version's **runtime** says how it runs: `acp` (a coding agent the runner ships), `code` (an
agent written in code, ADR 0063) or `a2a` (a remote agent: the runner pod calls it, a declared cost
is booked, the same gates read its outputs).

## Memory

What agents learn from work is kept in **memories** ([ADR 0075](adr/0075-an-agent-reads-several-memories.md)),
apart from knowledge bases (a corpus a human curates, read-only):

- every project has two **implicit** memories: **shared** (what the project knows) and **own** (what one
  agent learnt in that project, read by that agent only);
- a project owner may add **named project memories** (`decisions`), read by that project's runs only;
- an organisation administrator may add **global memories** (`security-lessons`), with a **reach**: every
  project, or a listed few.

An agent version **binds** up to eight of them, in order, each read or read-and-write
(`"memories": [{"scope": "global", "slug": "security-lessons"}, {"scope": "project", "slug": "shared",
"mode": "write"}]`). A `project` reference means "of whichever project the run is in", so an organisation
agent works in many projects without ever reading another one's memory. **Earlier wins**: when two
memories hold the same subject, the first is kept, and what fits the token budget is taken from each
memory in turn. At most one binding **writes**: the run's lessons and the agent's proposals go there; for
a **global** memory they are only ever **proposals**, accepted by an administrator. A version that
says nothing keeps its legacy `memory` field (`project`, `agent`, `none`), which means the same
bindings as before; nothing already published changes.

## Agent revisions and channels

A **revision** is one numbered, immutable version of an agent
([ADR 0071](adr/0071-an-agent-version-reaches-production-through-channels.md)). An agent has two
**channels**, each a pointer to a revision:

- **production** — what every project that pins nothing runs;
- **testing** — served to the projects that chose to follow testing, and to a **canary share** (a
  percentage of the work items of the other projects), picked by a hash of the work item so that all
  its stages and retries run one revision.

*Publish* saves a revision; *release* moves a channel. What saving does is the agent's policy (production,
testing or nothing; production by default, as before). Reaching production checks the eval suite again,
what testing measured (runs, success rate, time), and takes a person with a recent sign-in; a
**rollback** to a revision that was in production before skips the measurements, not the person. Every
move is kept in a history, and the run records the revision it used.

## Stage and run

A transition performed by an agent becomes a **run**: one disposable sandbox, one workspace,
one short-lived token, one model key with a hard cap. The runner speaks
**ACP** (Agent Client Protocol, from Zed — not IBM's Agent Communication Protocol, which merged into A2A; JSON-RPC over stdio) to the agent, records every message, applies the
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
action_succeeded · adr_number_free · already_fixed_proven · ci_green · coverage_delta_min
diff_size_max · docs_updated · evidence_facts · evidence_present · external · flag_present · markdown_sections
no_secrets · numbers_measured · output_schema · outputs_in · outputs_present
provenance_signed · review_approved · scans_ok · scope_respected · tool_called
```

`output_schema` checks each named output against a JSON Schema (2020-12) written in the workflow
(`schemas: {strategies: {...}}`); the validator refuses a schema that is not one, or one for an
output the transition does not declare. `evidence_facts` with `measured_only: true` refuses a fact
the platform did not measure (a job's facts, `dod.facts`): a threshold on a number the agent merely
wrote is not a guarantee. `numbers_measured` reads the numbers an agent writes in its outputs and
requires each to be found in the `sources:` documents an earlier step produced — rounding, a
percentage for a fraction and a dropped sign are tolerated; years, integers below `ignore_below`
(default 10) and an explicit `allow:` list are skipped. The source `fields` is the work item's own
fields, which a person can edit; a child opened by a fan-out reads `parent.<doc>[.<key>...]` instead,
the parent's measured document (a key `$name` is the child's own field `name`); a work item with no
parent has no such source and is refused. `must_quote:` names sources whose every number must also
be written in the outputs (and are sources themselves): a product memo must state the thresholds the
backtest applied (`parent.metrics.thresholds`), so a product that passed a lowered bar does not read
as one that passed the usual one (#824). A number of such a source counts as quoted when it is written
exactly (`0.95`, `35%`), wherever in the text: a `10` elsewhere quotes `min_trades: 10` — the gate
checks the numbers are there, not that the text names them as the bar.

`adr_number_free` guards numbered records: when the branch adds `docs/adr/NNNN-*.md` (the path
pattern is the `pattern` parameter), it lists the records already on the default branch and refuses
a number that one of them carries — two studies running together both pick "the highest plus
one", and the second to merge is sent back to renumber. The refusal names the files in question.
It is evaluated when the merge transition starts, alongside the other synchronous gates.

`docs_updated` guards documentation: the work item's **whole** branch diff must update a
documentation file whenever it changes anything else. Documentation is the `docs` parameter
(default `docs/**`, `*.md`, `*.mdx`, `*.rst`, `*.adoc`, `CHANGELOG*`); the `ignore` parameter lists
what needs none (default: tests, lockfiles, CI files); `min_files` (default 1) sets how many
documentation files must be updated. A documentation file counts when the diff adds text to it (or
changes it with no patch, which GitHub does for big files); a file that is only deleted does not. A
changelog line is enough. A change that touches nothing but tests, lockfiles and CI passes. It reads
the branch, not the writer's report, so a developer who already documented lets the writer change
nothing and still pass; it never takes an agent's word that nothing needed saying. The
`dev-complex` workflow of the `github-software-delivery` template (version 2) uses it on its
documentation step.

`already_fixed_proven` closes a ticket whose fix is already on the default branch, on measured
evidence only. It guards a `system` transition from the state an agent step starts from — the
platform's "already fixed" exit — and the interpreter takes that exit only after the step **measured
no change**: `diff_files` and `diff_lines` measured by git, both 0, no commit. The guarantee then
requires that no branch was pushed (read from the SCM) and that the runner ran the tests on that
unchanged checkout — the default branch — with at least one run and none failed (`tests_run`,
`tests_failed` in `evidence.measured`). Proven, the work item ends in the exit's terminal state, with
no test, review or pull request, and its issue is closed as completed with a comment citing the
evidence. Unproven, the step escalates at once: "the implementation changed nothing and the evidence
does not prove the fix already exists", followed by what is missing. The default selection never
takes such an exit, and the validator refuses one placed anywhere else
(`gate.already_fixed_not_an_exit`). The `dev-simple` workflow of the `github-software-delivery`
template (version 2) declares it as `t-already-fixed`.

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

## Network access

What a run may reach is bounded in three scopes that only ever narrow one another
([ADR 0070](adr/0070-network-access-is-bounded-and-enforced-per-run.md)): the **organisation**
(`network` in its policy ceiling: the widest allowed set, a deny list, rate caps), the **project**
(`sandbox.network` in its policy) and the **agent** (`network` in each published version). The
network of a run is their intersection, plus the host of the project's repository, minus every deny
list. Egress is always closed: an empty list means no internet. A host pattern `example.com` covers
that host and its subdomains, `*.example.com` only its subdomains, `*` any public host name; IP
addresses are never allowed. An agent that asks for a host its project or organisation does not allow
is refused when published, and its run is refused at admission. What each layer can and cannot stop is
in [security](security.md).

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

## Which model a stage uses

A stage asks for `profile:<tier>`, `profile:by_size` or a model name. The resolver looks in this order:
the **project's** profiles, the **organisation's tier** (`/admin/models`), the **platform** aliases
(`platform/<tier>`); then it checks the result against the gateway's list, the backend, the eval
matrix and, last, the **organisation's model catalogue** for the stage's role (a disabled model, or a
model without an entry in `curated` mode, is refused, and `allow_unvalidated` does not bypass it). See
[ADR 0060](adr/0060-a-gateway-agnostic-model-catalogue.md).

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
budgets, the ticket budget, the project's day budget (`daily_project_usd`) and the organisation's
month budget (`monthly_org_usd` in its policy ceiling), each minus what the cost ledger says was
spent (models and tools) and minus the keys still open at that scope (an agent's open keys count
against its day and month). Stages that reserve from the same scope are serialized (a PostgreSQL
advisory lock), so two stages preparing at once cannot both take the same remainder. Below
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

"Replay the stage" does not give a stage a fresh attempts allowance either: it leaves it one attempt
before its cap (`max_attempts`, lowered by the policy's `attempts.by_role`), so each rerun plays one more
run and escalates again if it fails.
An answer to a question that parked the work item resumes the stage the same way: one more
attempt, not a new allowance.
