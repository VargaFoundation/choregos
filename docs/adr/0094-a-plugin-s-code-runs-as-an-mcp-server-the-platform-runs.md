# 0094 — A plugin's code runs as an MCP server the platform runs and governs

- **Status**: accepted, 2026-10-27, by the product owner's requests of that day (plan
  `docs/plan/PLAN-2026-10-27-servicenow.md`, Decision 2, stream S43) — implemented by S43-06; the
  tracker half of the plan's ADR (Decision 3) is S43-07's
- **Concerns**: `packages/contracts/schemas/plugin-server.schema.json` (contract change: `paths`,
  `compensation` as an object, `platform_arguments`), `choregos_adapters.plugin_servers`, the API
  (`services/plugin_servers.py`, the installer, `services/connecteurs.py`, the broker, the
  `connector.call` effect, the organisation connector routes, `org_connectors.plugin_server`), the
  chart's `global.plugins.servers`, `infra/policies/plugin-servers.yaml`; amends
  [ADR 0034](0034-connectors-by-capability.md) (an organisation connector may be a plugin's server, whose
  declaration is the floor) and [ADR 0080](0080-hosted-prompt-and-voice-agents.md) (its image rules are
  shared); builds on [ADR 0050](0050-skill-supply-chain-gate.md) (the second approval),
  [ADR 0052](0052-netops-connectors.md) (`human_approval`, compensation, platform arguments),
  [ADR 0058](0058-connector-secrets-are-write-only-values.md) (sealed values),
  [ADR 0066](0066-the-organisation-egress-allowlist.md) and
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (network), and
  [ADR 0093](0093-an-agent-ships-as-a-plugin.md) (the plugin format)

## Context

ADR 0093 lets a plugin bring an MCP server (`servers/<name>.yaml`), pinned by digest, with the operations
it may serve. Until S43-06 the installer recorded such a server and ran nothing (`servers_pending`).
Connector code could only reach the platform as a `choregos.plugins` wheel in a derived image, which an
organisation cannot install, or as an MCP server someone else hosts and an administrator points an `mcp`
connector at. Writing the first real plugin (ServiceNow, `choregos-agents#2`) also showed what the
descriptor could not say: where the server answers, a compensation's arguments, and the arguments the
platform fills.

## Decision

1. **A plugin brings code only as an MCP server image, and the platform runs it.** On install (after the
   second approval, ADR 0050) the API creates, per organisation and server, a Deployment, a Service, a
   NetworkPolicy and a Secret in one namespace (`global.plugins.servers.namespace`, default
   `choregos-plugins`), through the `KubernetesClient` the executors use and a Role the chart grants in
   that namespace only. Applying is idempotent (absent → created, different → replaced, equal → left);
   an upgrade re-applies, a server the plugin no longer brings is removed, uninstalling deletes
   everything. A deployment that runs no servers refuses such a plugin.
2. **The pod is locked down.** Image pinned by digest; non-root, read-only root filesystem, every
   capability dropped, no privilege escalation, `RuntimeDefault` seccomp, no ServiceAccount token, no
   service links, resource limits, and the RuntimeClass the deployment names (by default the runner's,
   gVisor where it is set).
3. **The image rules are the hosted agents'** (ADR 0080 §8, amended): a registry prefix allowlist of its
   own (`plugins.servers.registries`, `CHOREGOS_PLUGIN_SERVERS_REGISTRIES`, matched by path segment), the
   signature policy and signer of `images.hostedCosign`. Checked before anything is written; in the
   cluster, the Kyverno policy `choregos-plugin-server-images` refuses a pod of the namespace that is not
   pinned or not under the allowed prefix.
4. **The server is an organisation `mcp` connector named after the server** (ADR 0034, amended). The
   agent's tools are therefore `<server>__<operation>`. A connector of that name the plugin does not own
   is a 409 when the install is asked, before anything is recorded. The connector keeps the server's
   declaration (`org_connectors.plugin_server`), written by the installer only; the connector routes
   never write it, and refuse to delete the connector (the plugin's uninstall does).
5. **The declared operations are the governance floor.** One `ConnectorOperation` per declared
   operation: a read allowed, a write under approval. `human_approval` is never lowered: the route
   refuses `allowed`, and `human_floor` re-applies it from the declaration whatever a row says. A tool
   the server serves that `operations` does not declare is refused at every door: discovery does not add
   it, the broker does not offer it, a governed action does not play it (`human_floor` answers
   `forbidden`). The declaration also answers `declared_operation` for compensation and platform
   arguments, the questions a connector type answers for pfSense.
6. **Authentication of `/mcp`.** The platform generates a token per server and organisation, seals it in
   the organisation's store under the connector's `_server_token` field (outside the secret routes' field
   pattern: nobody sets or reads it), gives it to the pod as `CHOREGOS_SERVER_TOKEN`, and presents it as
   a bearer token on every call. A server must refuse a call without it. The call goes to the Service
   URL the platform wrote, follows no redirect, and is not an egress of the organisation: it does not
   pass the organisation's egress allowlist, as an internet call would.
7. **The configuration is the server's.** Its `config_schema` and `secret_fields` replace the connector
   type's: an administrator types them on the connector (secrets sealed, ADR 0058), each change is
   applied to the server's Secret, and the digest of the Secret's data on the pod template rolls the
   Deployment. Until the whole schema is satisfied the Deployment has no replica (`unconfigured`). Each
   value reaches the pod as the variable `x-env` names, else the field in upper case; `CHOREGOS_*` is the
   platform's.
8. **Egress: the organisation's bounds at configuration, the server's own NetworkPolicy at run time —
   not the per-run proxy.** The hosts of `egress.from_fields` (or `egress.hosts`) are checked against
   the organisation's network bounds (deny list, allowed set, ADR 0070) when they are typed. The per-run
   egress proxy authenticates a *run* by its token and asks what that run may reach; a server is not a
   run and has no run token, and the organisation egress allowlist (ADR 0066) governs the platform's own
   pod families. So the server's NetworkPolicy admits ingress from the API's pods only, and lets egress
   through to DNS and to public addresses on the configured hosts' ports, never the private, link-local
   (metadata) or carrier-grade ranges. Kubernetes cannot name a host: with `plugins.servers.egress:
   cilium` a `CiliumNetworkPolicy` narrows egress to the exact names (`toFQDNs`) and the NetworkPolicy
   keeps DNS only.
9. **The descriptor says what the first plugin needed** (contract change): `paths: {mcp, health}`
   (defaults `/mcp`, `/healthz`); `compensation: {operation, arguments}`, the shape a connector type
   declares, its `arguments` rendered from the write's own result (`{{ result.sys_id }}`) — a template
   per argument rather than an `arguments_from` mapping, because the governed action already renders
   these templates for pfSense, so a plugin's compensation runs through the same code; and
   `platform_arguments` (`action_key`, `work_item_key`), filled by the platform, whatever the agent wrote
   under those names being dropped.

## Consequences

- An organisation installs connector code without an operator building an image, and governs it exactly
  as it governs pfSense: per-operation policy, approvals, compensation, audit.
- The plan's Decision 2 said a server's secrets "reach the server through the broker's call, never a
  run's pod". They reach the server's own pod, from a Secret in the plugins namespace, and never a run's
  pod: the server needs them to act between calls (polling, webhooks), and a credential sent with every
  call would cross the broker in clear each time. The Secret is readable by the API's Role and the
  namespace's administrators.
- The default NetworkPolicy does not name hosts: a compromised server can reach any public address on
  its configured ports. `cilium` closes that; elsewhere it is said, not hidden.
- The Kyverno allowlist is static: the operator keeps it and `plugins.servers.registries` in step.
- Objects written to the cluster are not part of the database transaction: a request that fails after
  applying leaves objects the next install, upgrade or configuration change converges; uninstall deletes
  them by name.

## Rejected

- **Running servers through the orchestrator as a long-lived workflow**: a server is a Deployment, not
  a run; Kubernetes already restarts it.
- **The per-run egress proxy for servers**: it authenticates runs; giving servers a run identity would
  invent a run that never ends.
- **A server's config sent with each broker call**: see Consequences.
- **A connector type per plugin registered at run time**: the registry is per process and per image; the
  declaration on the connector row reaches every replica.

## The tracker half (S43-07, the plan's Decision 3)

- **The tracker profile is a contract** (contract change): `plugin-tracker-profile.schema.json` gives
  the input and output of the eight tools a server that `implements: [tracker]` serves; the loader
  refuses such a server that does not declare one of them in `operations`, or declares a write as a
  read (it would then be allowed to every agent without approval). The core's `tracker/plugin`
  adapter checks every answer against the schema.
- **A delivery goes to `tracker_parse_event`, not to the server's `webhook.path`.** The plan said the
  core forwards it to `webhook.path`. The platform authenticates exactly one door of a server, `/mcp`
  with its bearer token (§6); `webhook.path` is the door the server opens for its system's direct
  posts (ServiceNow's `POST /events`), which the server's NetworkPolicy keeps closed to anything but the
  API anyway. One transport, one token, one place where answers are checked. `webhook` stays the
  declaration that a server takes deliveries (the procedure's URL placeholder, the 404 of the route).
- **The URL names the organisation** (`/webhooks/plugins/{org}/{plugin}/{server}`): a delivery is shown
  only to that organisation's server. Asking every organisation's server in turn until one's secret
  matched would show one organisation's deliveries to the others' servers and cost more with every
  organisation; the procedure's placeholder already knows the organisation, so nothing is typed twice.
- **Tracker calls are platform calls, audited, not governed actions**, like the platform's writes to
  Jira or GitHub: the workflow decided them (and puts a person before the stage that closes); each write
  is an audit entry `tracker.plugin.<tool>` in its own transaction. The declared policy still governs
  an agent calling the same tool through the broker.
- **Under `CHOREGOS_FAKES` the plugin tracker is built as itself** (`registry.BUILT_UNDER_FAKES`) and
  talks to the in-memory `FakePluginTrackerServer`, so the profile mapping is what the fakes exercise.
