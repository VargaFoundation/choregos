# 0034 — Connectors by capability, a policy per operation; a discovered MCP tool is born closed

- **Status**: accepted, 2026-10-05; amended 2026-10-06 (§7, a write is always a governed action); amended by [ADR 0074](0074-catalogue-rights.md) (availability per project)
- **Concerns**: the connector registry (`register`), the `connectors` table, `GET /connectors/types`,
  project settings, a new `connector_operations` table, the internal tool broker, the tool
  catalogue of [ADR 0014](0014-a-platform-held-tool-catalogue.md) (amended), the MCP
  sidecar; follows [ADR 0029](0029-a-platform-for-governed-agents.md) and
  [ADR 0033](0033-agent-registry-and-skill-library.md)

> **Amended by [ADR 0094](0094-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md)**: an
> organisation `mcp` connector may be a plugin's server, named after it; its declared operations are the
> floor the broker enforces (`human_approval` never lowered, an undeclared tool refused), and its
> configuration is the server's own schema.

## Context

A project's settings show `scm`, `ci`, `cd`… The product review asked what an HR project — Entra,
a device manager, a carrier, badge readers, a supplier's agent — would do with them. Nothing:

- eight connector kinds are hard-coded, in an enum and in the page, configured as raw JSON while a
  `config_schema` is declared and ignored; `GET /connectors/types` contradicts the registry;
- a project holds one connector per kind;
- `secret_ref` is stored and never read;
- every run gets two fixed MCP servers; the tool catalogue is an operator's YAML, one entry per
  tool, with no `initialize` and no OAuth — a real MCP server cannot be declared at all.

## Decision

1. **A connector type declares capabilities, not a kind.** `register(..., spec=…)` names its
   capabilities (`scm`, `tracker`, `identity`, `mdm` (device management), `shipping`, `access_control`,
   `mcp`…), the JSON Schema of its configuration, its secret fields, its operations and its fake.
   `GET /connectors/types` reads the registry; the console renders the configuration from the schema
   (the `SchemaForm` of ADR 0032). A project may hold several connectors, named.
2. **A project shows what its workflows require.** `GET /projects/{id}/requirements` is derived
   from the workflows (a train needs `cd`, an `identity` action needs an identity connector): a
   project without a repository or a train shows neither `scm` nor `ci` nor `cd`.
3. **Secrets are references, resolved by a seam — or sealed values (ADR 0058).** `secret_ref` is read
   through `SecretResolver` (`env:` in the core; a vault in a plugin). A secret written in clear in a
   configuration is refused (422). A value typed in the console is sealed in the database and replaced
   by a derived `store:` reference ([ADR 0058](0058-connector-secrets-are-write-only-values.md)).
4. **Every operation carries a policy**, in `connector_operations`: read or write; `allowed`,
   `approval` or `forbidden`; the groups that may use it; a price; the digest of its schema. A
   project can only **tighten** an operation (an `approval` never becomes `allowed` below the
   organisation), and only the organisation's administrator changes groups.
5. **The `mcp` type connects to a real MCP server**: `initialize`, `tools/list`, `tools/call`, over
   Streamable HTTP or SSE, with a bearer or client credentials by reference. Discovery computes a
   diff. **A discovered tool is born closed**, and a change of its input schema **closes it
   again**: a server cannot add or widen a capability behind the administrator's back.
6. **Runs reach MCP servers through the platform.** A broker behind `/internal` merges the
   catalogue, the plugins and the operations; the tools a run sees are the intersection of the
   policy, the groups, the activation and the agent's selection (ADR 0033). The run token stays
   the only credential in the pod; the server's key reaches the server, never the pod. Each call
   is costed (`provider=mcp:<connector>`) and filtered for injection.
7. **A write is always a governed action** (amendment of 2026-10-06, issue #241). A **read** that
   the policy allows is a direct call. A **write** — even `allowed` — is not: calling it proposes
   a governed action (ADR 0035) that the policy approves at once (`by: policy`), recorded under its
   key, done once, compensated if a later step fails, and played by the `ActionWorkflow`, never by
   the request. The broker waits for its outcome, bounded (`courtier_attente_ecriture_s`, 30 s), to
   hand the agent what the server answered — beyond that, `202` and the action's id. The same call
   (run, tool, arguments) is the same action: an agent that repeats it does not write twice. Before
   the amendment an `allowed` write was a direct call: no journal, no compensation, and a retried
   call wrote twice unless the remote happened to be idempotent — the governance promise of
   ADR 0029 held for approved writes only.

## Consequences

- The YAML catalogue of ADR 0014 becomes one source among others, read through the same broker;
  its guarantees (no wide token in a pod, every call in the ledger, a cap per run) now hold for
  every MCP server.
- An operation set to `approval` turns a tool call into a governed action (ADR 0035) that waits
  for a person; a write set to `allowed` turns it into one that the policy approves (§7).
- The connector pages are generated, so a connector type a plugin adds needs no console change.

## Amendment (S40-05): a platform default is claimed only for what the deployment provides

`GET /projects/{id}/requirements` returned `default_type = type_par_defaut(kind)` for a capability
with no connector: `argocd` for `cd`, `tekton` for `ci`, `slack` for `notify`. Those are the types the
adapter factory would build with an empty configuration, which provide nothing (no GitOps repository,
no token), yet the console printed "Platform default: argocd" and marked the getting-started step done.
`default_type` now comes from `deployment_default_type(kind)`: the deployment's executor for
`runtime`, `CHOREGOS_GATEWAY_TYPE` for `gateway`, `ecphoria`/`lexical` for `memory`, and null for every
other kind. `type_par_defaut` keeps its meaning for the factory, so run behaviour does not change.
A connector is also tested when saved, so its status is no longer `unknown` until somebody presses *Test*.
