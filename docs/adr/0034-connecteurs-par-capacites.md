# 0034 — Connectors by capability, a policy per operation; a discovered MCP tool is born closed

- **Status**: accepted, 2026-10-05; amended 2026-10-06 (§7, a write is always a governed action)
- **Concerns**: the connector registry (`register`), the `connectors` table, `GET /connectors/types`,
  project settings, a new `connector_operations` table, the internal tool broker, the tool
  catalogue of [ADR 0014](0014-un-catalogue-d-outils-tenu-par-la-plateforme.md) (amended), the MCP
  sidecar; follows [ADR 0029](0029-une-plateforme-d-agents-gouvernes.md) and
  [ADR 0033](0033-registre-d-agents-et-bibliotheque-de-skills.md)

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
3. **Secrets are references, resolved by a seam.** `secret_ref` is read through `SecretResolver`
   (`env:` in the core; a vault in a plugin). A secret written in clear in a configuration is
   refused (422).
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
