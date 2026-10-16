# 0040 — A built-in agent catalogue: installed on demand, or when a workflow names one

> **Amended by [ADR 0061](0061-agent-ownership-scopes-and-forks.md)**: an agent installed from the
> catalogue is a common (`platform`) agent, changed by an `org_admin` only; to extend it, fork it.

- **Status**: accepted, 2026-10-07
- **Concerns**: `packages/core` (`choregos_core.catalogue_d_agents`), the agent registry API, the
  console's `/agents`, the shipped templates; amends [ADR 0033](0033-agent-registry-and-skill-library.md)

## Context

ADR 0033 made agents registered, versioned objects of an organisation. The product review of
2026-10-07 found `/agents` showing only the two HR coordinators: they are the only agents any
template installs. The software workflows — the main use case — run *implicit* agents (a role and a
playbook), which are never registered, so they have no metrics, no budget, no versions, and nobody
can find, tune or reuse them. Nothing told a new organisation which agents exist or how to get one,
and nothing listed the AI clients that can act through the MCP gate.

## Decision

1. **The platform ships a catalogue of agents** (`choregos_core.catalogue_d_agents`): one entry per
   playbook role (triage, refine, plan, implement, verify, review, address_review, fix_ci,
   release_notes, verify_prod) plus what a study needs (researcher, architect) and a security
   reviewer; the MADR 4 skill the architect and the reviewer carry; and the external clients that
   reach in through the MCP gate (Claude Code, Claude Desktop, Cursor, GitHub Copilot in VS Code,
   ChatGPT, claude.ai). An entry is the document `POST /orgs/{org}/agents` accepts, with a version.
2. **"Usable today" only.** An internal entry fixes neither a model nor a runtime: it inherits the
   project's and the deployment's (S20-11), so it runs wherever Choregos runs. A client that calls
   from its vendor's cloud is offered only when the MCP gate accepts OAuth, a client is registered
   and the address is https. Nothing is listed that would not work.
3. **The work item's context is part of an entry.** At load time, every internal entry's
   instructions are preceded by the context block (work item, approved specification, plan, allowed
   paths, the transition's inputs): an author writes the task, not the plumbing.
4. **Installing never rewrites.** An organisation installs an entry as version 1 of its own agent;
   an update publishes the next version only when the entry changed, and project pins stay where
   they are. An organisation's own agent with the same slug wins over the catalogue.
5. **A workflow that names a catalogue agent installs it** when it is published or a project is
   born from a template that names it (`catalogue:<slug>`): an `agent:` naming an absent agent would
   otherwise kill the work item at its first run (`AgentIndisponible`).
6. **External agents are one click**: connecting a client creates its external agent (once per
   organisation), mints a `mcp:*` token for the caller and attaches it.

## Consequences

- The shipped software templates can name registry agents (`agent: developer`), and their runs get
  per-agent metrics, budgets and versions like any other agent.
- The catalogue is English (ADR 0039) and grows by pull request, like the playbooks.
- An entry's text changes with the platform's releases; organisations get it only by updating
  explicitly, never silently.
- **Specialists (S33-06, plan item 3).** An entry may state its **output contract** (`catalogue.outputs`:
  the names a transition running it declares), the **tools** it acts with (`catalogue.tools`: runner
  binaries, read-only connectors, skills, knowledge) and whether its result must go to people next
  (`catalogue.human_gate_after`). The catalogue adds `secops` (the runner's scanners, interpreted,
  SARIF out), `compliance` (licences, personal data, third-party terms; never legal advice, always
  followed by a human gate), `devops` (release readiness and rollout plan; reads observability after the
  train; never deploys), `docs-writer`, `ux-designer` (the organisation's design system as a skill or a
  knowledge base; axe when the project can run it) and `qa` (edge cases and mutation testing — mutmut
  and Stryker are in the runner image). `architect` challenges a request against the knowledge bases
  attached to it, citations mandatory. `netops` is its own entry (S33-08).
- **Every internal entry has an eval** (`catalogue_d_agents/eval_cases/`, ADR 0020): deterministic
  assertions on its rendered instructions, run by `make evals` and the nightly job, and proved to turn
  red when the instructions are emptied (`packages/core/tests/test_catalogue_evals.py`). An agent with
  no eval does not enter the catalogue.

## Alternatives discarded

- **Registering the playbooks as agents automatically** in every organisation: the registry would
  fill with agents nobody chose, and a playbook change would rewrite them silently.
- **Listing third-party autonomous agents** (Devin, Jules, GitHub Copilot's coding agent): each needs
  its own delegation adapter; listing them before it exists would show agents that do not work.

## Update — 2026-10-16 (S40-10)

A catalogue entry's `agent:` document carries `tags` (labels, ADR-free metadata outside the version).
Installing sets them; an upgrade **adds** the tags the agent lacks, even when the spec did not change,
and **never removes** one: an organisation's own tags are not silently rewritten, the rule of §4.
