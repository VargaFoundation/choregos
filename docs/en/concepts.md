# Concepts

Seven ideas carry the whole platform. Everything else is plumbing.

## Project

A project is a unit of work with its own connectors, workflow, policy and budgets. It lives
in an organisation. Its configuration (`ProjectConfig`) names the repository, the model
profiles and the labels; its **connectors** say which outside systems it talks to.

Connectors ship for: trackers (`github-issues`, `jira`, `gitlab-issues`, `internal`), source
control (`github`), CI (`tekton`), CD (`argocd`), execution (`tekton`, `k8s_job`, `aca`,
`local_docker`), model gateway (`litellm`, `direct`), memory (`ecphoria`, `pgvector`) and
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

## Actor

Three kinds:

- **`agent`** — a role and a playbook, a model profile, and budgets in turns and minutes.
- **`human`** — a group, an SLA in hours, a reminder and an escalation.
- **`system`** — a transition the platform performs itself, with no agent and no cost.

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
ci_green · coverage_delta_min · diff_size_max · evidence_facts · evidence_present
external · flag_present · no_secrets · outputs_present · provenance_signed
review_approved · scans_ok · scope_respected
```

**A gate that cannot see what it must check refuses.** If the SCM connector cannot produce a
diff, `scope_respected` does not pass — it declines. A guarantee that passes blindly is worse
than no guarantee, because it reassures.

## Evidence

What a step leaves behind, in the result: tests run and passed, lint, typing, coverage delta,
diff size — and `facts`, a flat map named by the business for work that has none of those.
Evidence is what gates read and what the UI shows; it is not prose.

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
what is ready, opens its window, requires its approval, and can be frozen with a reason.

## Where the money goes

Every model call is minted against a per-run virtual key with a hard cap, and lands in a cost
ledger with its project, ticket, stage and model. Tool calls from the catalogue land in the
same ledger under `kind: tool`. Budgets are declared in the policy: per ticket, per stage, in
turns, in minutes, in tool calls.
