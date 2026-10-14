# 0014 — A platform-held tool catalogue

- **Status**: accepted, 2026-09-24
- **Concerns**: the internal API, the MCP sidecar, the policy, the cost ledger

## Context

The HR demo of 2026-09-23 stopped on "no candidate profile database reachable". The agents
were right to block: it was not an engine defect, it was an absence of **data**. An agent
that processes a case, searches for a profile or checks a company needs third-party APIs —
and those APIs have keys.

[treg](https://github.com/superdesigndev/treg) answers exactly that gap: a directory of
3,000 tools behind a proxy that injects credentials server-side, billed per call. The
question was: integrate it, or take the idea?

## Decision

**We take the idea, we do not add the dependency.** Choregos has to be a platform of one
piece, not an assembly of third-party projects each of whose upgrades is a risk.

The catalogue is therefore ours, and it rests entirely on pieces that already existed:

| Need | What carries it |
|---|---|
| Declaring tools | A YAML file brought by the deployment (ConfigMap), reviewed in a PR |
| Holding the keys | The API's environment, by reference to a Secret |
| Identifying the caller | The **run token**, already minted, already short, already verified |
| Exposing to the agent | The `choregos-tools` MCP sidecar, already wired and already on localhost |
| Bounding spend | `budgets.tool_calls_per_run`, like turns and minutes |
| Counting | `cost_ledger`, with a `kind` column that tells model from tool |

## What this settles that treg did not

1. **The wide token.** treg issues one token per member, valid for *all* tools, with no cap
   per key. In an agent pod that is exactly what AGENTS.md forbids. Here the agent holds no
   provider credential: it carries its run token, which is worth only its run and expires
   with it.
2. **The cost hole.** A call billed outside the platform is spend nobody sees or caps. Every
   call lands in the ledger with its run, project and stage — and the cap refuses the next
   one, without discussion.
3. **The agent's egress.** The platform goes out to the provider, not the agent pod, whose
   allowlist stays closed. An agent cannot reach the provider directly even if it wanted to.

## Decision 2 — An external MCP server: its key, not ours

A catalogue tool can come from an **MCP server outside the organisation** (`mcp:` instead
of `http:`). Three rules, each answering one way of getting hurt:

1. **The run token never leaves.** It authenticates the agent *to us*: handing it to a third
   party would give them what it takes to write into the platform — post a result, file a
   finding, widen a scope. The remote server receives `credential_env`, a key worth only
   for it. A test checks this by searching for the token's value in the header **and** the
   body of the outgoing request.
2. **We expose only part of a server, under our names.** The catalogue declares the remote
   name (`mcp.tool`) separately from the exposed name. This indirection lets us publish
   three tools of a server that offers sixty, and name them in the house vocabulary. The
   agent never sees the URL nor the remote name.
3. **Every tool opens to groups.** `groups: [rh]` on the tool, `groups: [rh]` on the
   project. **Two locks, and they do not say the same thing**: the deployment says WHO is
   entitled, the project says what IT uses. Without the first, the project's list would be
   the only control — and it is editable by the project team itself. A tool without
   `groups` restricts nothing: the restriction is added, not imposed.

What it does not do: the catalogue does not query the remote server to discover its tools.
The list is written, reviewed in a PR, and does not change because the provider shipped
something new. A directory that updates itself is not an access control.

## What is deliberately absent

- **The URL is never chosen by the agent.** It fills `{{ field }}` templates defined by the
  catalogue; it sets neither host nor method. Without this rule the catalogue would make the
  platform an open proxy calling anything with its own keys — including an instance
  metadata service.
- **No default tool.** A project declares what it may call; the empty list is the default,
  and an undeclared tool returns **404**, not 403: an agent has no business discovering the
  deployment's catalogue by trying names.
- **No directory.** The day we want the breadth of a public catalogue, it is **a catalogue
  tool** that leads there, with its key and its cap. The platform does not depend on it.

## Consequences

- `GET /internal/runs/{id}/tools` and `POST /internal/runs/{id}/tools/{name}`, with the run
  token.
- The MCP sidecar mixes catalogue tools with its own: the agent sees them as ordinary
  tools. An unreachable catalogue does not deprive it of `report_finding` and the others.
- `global.toolCatalog` in the chart: a ConfigMap for the catalogue, a Secret for the keys.
- `demo/outils/catalogue.yaml` holds **one** real, keyless tool — the French government's
  Adresse API — so the chain can be shown without asking anyone for an account.
- A migration adds `cost_ledger.kind`, with a `server_default`: autogeneration produced a
  `NOT NULL` column without default, which PostgreSQL refuses on a populated table.
