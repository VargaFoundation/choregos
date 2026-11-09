# 0127 — An agent is a closed set: instructions, model, tools, knowledge, memory

- **Status**: proposed, 2026-11-09, by the product owner's request of that day — contract S60-01;
  implemented by S60-02 (runs), S60-03 (console), S60-04 (catalogue and templates), S60-05
  (publication). Amends [ADR 0033](0033-agent-registry-and-skill-library.md) §2 (the project only
  tightens: it now holds for tools and model, where it did not) and
  [ADR 0040](0040-a-built-in-agent-catalogue.md) (an internal agent fixed neither model nor tools)
- **Concerns**: `AgentSpec.tools` (`catalogue`, `extensions`, `web`), `AgentSpec.model`,
  `AgentSpec.memories`, `AgentSpec.knowledge` (`choregos_api.schemas.agents`, `openapi.yaml`);
  `GET /orgs/{org}/agents/{slug}/access`; `GET /internal/runs/{id}/tools` and
  `POST /internal/runs/{id}/tools/{name}`; `prepare_stage` (the model a run asks for); the console's
  agent definition, access tab and builder; the catalogue plugins of `VargaFoundation/choregos-agents`
  and the agents of `templates/`

## Context

The product owner defined an agent as one set: **instructions, a model, tools and MCP servers,
knowledge, and a memory**. On 2026-11-09 the access tab of `developer` in production read *"Its version
names no server: it reaches what the platform gives a run of its project"*.

Surveyed on `main` at `0c3b5772`, what a run of an internal agent receives and where it comes from:

| Component | Declared by the version | Where a run actually gets it |
|:--|:--|:--|
| Instructions | `instructions`, `skills` | the version |
| Model | `model`, optional | else the workflow actor's `model`, else `profile:by_size`, resolved by the project's profiles |
| Tools | `mcp_servers` (the broker), `tools.scripts` | plus every catalogue tool the project's `tools` and `groups` open, every extension's tool, `web_fetch` when the run's network allows a host, `web_search` when the project has a backend |
| Knowledge | `knowledge`, `skills` | the version |
| Memory | `memories`, optional | else the legacy `memory`, `project` by default |

Of the 29 agents the offline catalogue plugins ship, none names a model, a memory or a knowledge base;
one names MCP servers (`netops`), two name skills. `agent_catalogue/__init__.py` says it plainly: *an
internal agent fixes neither model nor runtime: it inherits the project's and the deployment's.* The same
`developer@1` therefore does different things in two projects, which its evals, its version diff and its
authorship attestation (ADR 0015) cannot see.

## Decision

1. **An agent version is the set of five components**, and each is the version's to declare:
   - *instructions* — `instructions`;
   - *model* — `model`: a model name or a tier, `profile:<tier>` (ADR 0060 §5);
   - *tools* — `mcp_servers` (connectors, through the broker) and `tools`: `catalogue`, `extensions`,
     `web`, `scripts`;
   - *knowledge* — `knowledge` (the bases it reads, ADR 0051) and `skills` (the procedures it carries,
     ADR 0033 §5);
   - *memory* — `memories` (the memories it binds, ADR 0075), `[]` for none.
2. **`AgentSpec.tools` gains three lists**, each optional:
   - `catalogue`: the deployment catalogue's tools (ADR 0014) the version uses, by name or as
     `group:<name>`;
   - `extensions`: the extensions (`choregos.extensions` tool providers) whose tools it uses, by name;
   - `web`: `fetch`, `search` — the governed web tools (ADR 0070, ADR 0083).
   A list that is present, even empty, **closes** that source: a run is offered what the version names
   **and** what its project and the deployment allow — never more. A list that is absent keeps
   today's behaviour (the project's) and, as `memories`, `workspace` and `tools` already are, is left
   out of the stored spec, so no published version's digest moves.
3. **The project only tightens** (ADR 0033 §2). A catalogue tool the version names that the project's
   `tools`/`groups` do not open is not offered; a `web` tool the run's network or the project's backend
   cannot serve is not offered. Neither is an error at run time: the access endpoint (6) says it in
   advance, by name and with the reason.
4. **The model is the version's choice.** A version that names a model or a tier has it: the
   workflow actor's `model` no longer replaces it (it already did not, `spec.model or plan.model_request`).
   A tier resolves as ADR 0060 §5 says — the project's profile, then the organisation's tier, then the
   platform's — because that resolution says *which* model serves a tier, not *which tier* the agent
   asked for. A version that names none keeps today's chain and is reported as inheriting it.
5. **The frame is not a tool of the agent.** The platform's own tools — the `choregos` server's
   `validate_result`, `report_finding`, `request_scope_change`, `ask_human`, `get_ticket`, `get_context`,
   `search_memory`, `knowledge_search`… — are the contract between a run and the platform (ADR 0033 §4):
   every run has them, the access endpoint lists them under `frame`, and no version names them.
6. **What a version reaches in a project is read before it runs.**
   `GET /orgs/{org}/agents/{slug}/access?project=&version=` answers, for one version (the current one by
   default) in one project, each component with its **origin**: `agent` (the version names it),
   `project` (the version leaves it to the project: inherited), `frame` (the platform's), and for what
   the version names but will not get, `refused` with the reason. It also says which components the
   version leaves to its project (`inherits`) and whether it is `complete`. The run's own tool listing
   and this answer are computed by the same function: the console cannot show what a run would not get.
7. **A version is complete when nothing of it is inherited**: `model` set, `tools.catalogue`,
   `tools.extensions` and `tools.web` present, `memories` present (even empty). Instructions and
   knowledge are never inherited (every stored spec holds `knowledge`, empty or not), so they need no
   rule. A remote (`a2a`) agent is complete by construction (it may declare none of them); a `voice`
   agent needs only its model, which its runtime already requires — a session reaches its MCP servers
   and nothing else; a `hosted` agent declares its model, tools and memories as any other.
8. **Publication requires completeness (S60-05)**, once the catalogue plugins and the templates the
   core ships declare everything (S60-04): a new version of an internal agent that leaves a component
   out is a 422 `agent_incomplete`, naming the components. Published versions are immutable and keep
   running as they are; the console marks them *inherits from the project* until their next version.
   An external agent (a client of the MCP door) is not concerned: its rights are its human's,
   intersected with its own (ADR 0033 §7).

## Consequences

- The access tab shows what a run of the version gets in a chosen project, source by source, with its
  origin; the definition shows the five components, and *inherited* where the version says nothing.
- The catalogue plugins publish new versions that declare what their agents received until now, so
  installing them changes no run (S60-04); their evals stay the gate.
- A project that relied on opening a catalogue tool to every agent must now find it named by the agents
  that use it; the access endpoint lists the projects' tools no complete version names.
- Not covered: the coding agent's own tools (shell, file edits of an `acp` backend) are bounded by the
  workspace (ADR 0119) and the kernel's confinement (ADR 0094), not by this list.

## Alternatives rejected

- **Keep the inheritance and only show it.** The screen would be right and the definition wrong: two
  projects would still run two different agents under one version.
- **A new top-level field per source.** `tools` already holds what a run calls (ADR 0122 §9); one
  object per component keeps the five readable in a spec.
- **Refuse incomplete versions at once.** Every catalogue agent and template would stop publishing the
  day the contract lands; completeness is required only after they declare.
