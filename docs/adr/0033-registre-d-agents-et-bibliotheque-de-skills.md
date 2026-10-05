# 0033 — Agents are registered, versioned objects; skills are a library they carry

- **Status**: accepted, 2026-10-05
- **Concerns**: new tables `agents`, `agent_versions`, `project_agents`, `agent_credentials`,
  `skills`, `skill_versions`; the workflow DSL (`AgentActor.agent`); `prepare_stage`; the runner's
  workspace; the playbooks; the console's Agents pages; follows
  [ADR 0029](0029-une-plateforme-d-agents-gouvernes.md) and [ADR 0030](0030-une-porte-mcp-pour-les-clients-externes.md)

## Context

The product review asked: *this is an agentic platform — where are the agents?* Today an agent is
a line in a workflow — `{type: agent, role: implement, model: …}` — and its instructions are a
playbook file chosen by the deployment, editable by nobody and visible nowhere. Nothing records
which instructions ran, nothing gives an agent a budget of its own, and a Claude Code connected to
the MCP door (ADR 0030) is invisible as an agent. The word *skill* appears nowhere, while every
agent backend we run now reads skills from its workspace.

## Decision

1. **An agent is an object of the organisation**, `agents`: a slug, a kind (`internal` — run by
   the platform — or `external` — a client of the MCP door), an owner, a status, an expiry, a
   revocation. What it *is* lives in **immutable versions**, `agent_versions`: instructions,
   model, backend, limits, skills, MCP servers and tool patterns, budget. A change publishes the
   next version; nothing is overwritten.
2. **A project pins a version** (`project_agents`) and may only **tighten** it: a lower budget,
   fewer tools. An override that widens is refused (422).
3. **A workflow names an agent**: `AgentActor.agent: slug[@version]`. `prepare_stage` resolves it;
   the agent's instructions win over a playbook of the same name; the run records `agent_id` and
   `agent_version`. The playbook stays as the fallback, so every existing workflow keeps running.
4. **Instructions are templates, rendered in a sandbox.** Jinja `SandboxedEnvironment`, inside a
   frame the agent cannot edit: the output contract, the scope, the safety rules. An agent's
   author writes *what to do*, never *how to answer the platform*.
5. **Skills are a library of the organisation**, `skills` and `skill_versions`: a folder (at most
   64 files, 512 KiB), imported as a zip or edited in the console, versioned and immutable like
   agents. **A skill declares no permission**: an `allowed-tools` field is refused — what an agent
   may call is decided by its version and its project, never by a file it reads. Zip-slip,
   symbolic links and a name that disagrees with the folder are refused.
6. **The runner materialises the skills an agent carries** where its backend looks for them
   (`.claude/skills`, `.opencode/skills`, `.agents/skills`, `.gemini/skills`, `.goose/skills`,
   `.github/skills`), checked against their digest; a backend without skills gets an index in its
   prompt. Skills stay out of the diff.
7. **External agents are agents too.** A token of the MCP door, or an OAuth client, is attached
   to an `external` agent (`agent_credentials`): the agent's rights **intersect** the human's —
   an agent acting for someone can do neither more than the agent nor more than that person —, its
   use is counted per day, and its revocation is read on every call.
8. **A proposal carries the agent's identity** (`agent:<slug>`), not `agent:platform`. Whether an
   agent's owner may approve its writes is decided per operation (ADR 0034); by default not for a
   sensitive one.

## Consequences

- The Agents pages show, per agent, its versions, runs, cost and success rate — including the
  Claude Code sessions connected through the MCP door.
- A playbook becomes the default of a role, not the only place instructions live; the
  deployment-wide playbook directory keeps working.
- Four replayed histories guard the interpreter change of decision 3; the runner change of
  decision 6 is tested per backend, since the runner installs three of them.
- New surface, each with its red test: editable instructions (sandbox escape), executable skills
  (no permission declared, digest checked), delegated rights (intersection).
