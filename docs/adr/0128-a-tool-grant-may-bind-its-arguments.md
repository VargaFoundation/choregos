# 0128 — A tool grant may bind its arguments

- **Status**: proposed, 2026-11-09, by the product owner's request of that day — contract S60-06.
  Amends [ADR 0034](0034-connectors-by-capability.md) (the broker checked the operation's schema only) and
  [ADR 0127](0127-an-agent-is-a-closed-set.md) (what an agent reaches)
- **Concerns**: `AgentMcpServer.tools` (`choregos_api.schemas.agents`), `connector_operations.arguments`,
  `project_operation_policies.arguments`, `services/broker.py` (`tools_of_selection`, `check_arguments`),
  `GET /internal/runs/{id}/tools`, `POST /internal/runs/{id}/tools/{name}`,
  `PATCH /orgs/{org}/connectors/{name}/operations/{operation}`,
  `PUT /projects/{id}/operations/{connector}/{operation}`, `openapi.yaml`

## Context

The product owner, 2026-11-09: "MCP grants must also constrain the MCP's parameters: allowing `sync` on Argo CD is too broad, but on one application it is fine" (translated). Allowing an agent to
call Argo CD's `sync` on every application is too broad; on one application it is fine.

Today a grant says **which operation** (a name or a `prefix_*` pattern), the organisation says **whether**
it is `allowed`, `approval` or `forbidden`, and the project may only narrow that. None of them says
**with what**: `check_arguments` validates the call against the operation's `input_schema`, which is
whatever the server declares and is the same for every agent and every project. The only way to bind an
argument was to write a second MCP server.

## Decision

1. **A grant may carry an argument constraint**: a JSON Schema object (draft 2020-12) the call's
   arguments must **also** satisfy, on top of the operation's `input_schema`. No `$ref`, `$dynamicRef`,
   `$id` or `$anchor`: validating it reads nothing but the schema and the arguments. At most 8 KiB of
   JSON. `{project}`, the placeholder S59-11 already uses for an approver group
   (`choregos_core.dsl.groups`), is the only one: it is replaced by the run's project slug in every
   string value before the arguments are validated. Any other brace is literal (a `pattern` may use
   `{2}`).
2. **Where it is written**, each level only narrowing, **all** constraints that apply passing
   (intersection):
   - **the agent version**: an item of `mcp_servers[].tools` is a string, as before, or
     `{name: <name or prefix_* pattern>, arguments: <schema>}`. Additive: a version that keeps strings
     serialises, and digests, as it did. Every constrained grant that matches an operation binds it; a
     plain pattern that also matches does not lift it (fail closed);
   - **the organisation's operation rule**: `connector_operations.arguments`, set by
     `PATCH …/operations/{operation}` (`null` clears it); it binds every agent in every project;
   - **the project's operation policy**: `project_operation_policies.arguments`, set by
     `PUT /projects/{id}/operations/{connector}/{operation}` (absent clears it), narrowing on top of both.
3. **Enforcement** is in the broker's `check_arguments`, which every path calls before anything else
   happens: the read call, the proposal of an operation under approval, and the governed write. A call
   that satisfies the operation's schema but leaves a constraint is refused `400` with the problem type
   `arguments_outside_grant`, whose members `level` (`agent`, `organisation`, `project`) and `path` (the
   JSON path of the first place the arguments leave it) say where. Nothing is proposed, journaled or sent
   to the server, and the call is not billed. A refusal ends the request as the operation's own schema
   refusal does (its span carries the 400; no `tool.called` event, which says a call was made). The tool
   a run is announced (`getRunTools`, and a session's tool listing) carries
   `{"type": "object", "allOf": [input_schema, …constraints]}`, so the model learns the bound instead of
   discovering it by being refused.
4. **Refused when written** (ADR 0101: governance nobody applies is refused, not hoped for): a constraint
   that is not a valid schema, that uses a forbidden keyword or exceeds the size is a 422 at every level;
   one that names (in `properties`, `required`, or inside `allOf`/`anyOf`/`oneOf`) an argument the
   operation's `input_schema` does not declare is a 422; an agent grant whose pattern matches no operation
   of an organisation connector is a 422 (it would bind nothing); two constrained grants of one version
   matching the same operation are a 422 (they would both bind, so their intersection is usually empty:
   one grant with `anyOf` says the alternatives). A restore of a published version is not judged again.
5. **Not covered.**
   - Catalogue tools, extension tools and the web tools: they have no arguments policy yet; a constraint
     on them is refused (no operation to bind).
   - **Semantic aliases**: the constraint is on what the call sends. If an application can be addressed
     by another argument (an id instead of a name, a label selector), the constraint must bind that
     argument too; the platform does not know that two arguments name one thing.
   - A server that drifts: discovery closes an operation whose schema drifts, but a constraint
     naming an argument that disappeared stays stored; with `required` it refuses every call, otherwise
     it no longer binds that argument.
   - The cost of a `pattern`: it is a regular expression run on the arguments, bounded only by the
     constraint's size.
   - `GET /orgs/{org}/agents/{slug}/access` (ADR 0127) does not show a tool's constraint yet.

## Consequences

- One Argo CD connector serves an agent that may sync `shop` and another that may sync `billing`;
  the organisation can still forbid `revision: main` for all, and a project can narrow further.
- A version's definition gets heavier: the console must show a constraint beside the tool (a later story,
  with `/access`).
- The announced schema grows with the constraints; a tool listing stays under the 8 KiB per level.

## Alternatives rejected

- **One MCP server (connector) per application.** Multiplies connectors, keys and discoveries for what is
  one policy.
- **Only the organisation's rule.** It cannot tell two agents apart: the one that syncs `shop` and the one
  that syncs `billing` would share a rule.
- **A constraint language of our own** (`allow: {name: [shop]}`). It would be a second schema dialect to
  document and to test; JSON Schema is already what the operation declares and what the model reads.
- **A plain pattern lifting a constraint** (union of grants). A sloppy `*` next to a careful grant would
  silently open everything.
