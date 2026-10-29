# 0088 — A reference to a deployment secret is the operator's, an organisation types its keys

- **Status**: accepted, 2026-10-26 — implemented by S39-06f; amends
  [ADR 0058](0058-connector-secrets-are-write-only-values.md) §10 (RBAC) and the S39-06b rule that
  an organisation's administrator writes `env:` references
- **Concerns**: `verifier_les_secrets` (`choregos_api.services.connecteurs`), the connector write
  routes (organisation, project, MCP catalogue install), `choregos_api.deps.is_instance_admin`

## Context

A connector secret is held in one of two places:

- **sealed in the organisation's vault** (`store:`, ADR 0058): typed in the console, encrypted under
  the deployment's key, bound to the organisation by RLS and authenticated data;
- **in the deployment**: `env:NAME` reads the environment of the API and worker pods (a plugin
  scheme such as `vault:` reads the operator's vault). Those pods serve **every** organisation.

Until now any organisation's administrator could write a deployment reference. The allowlist
(`CONNECTOR_*`, S39-06b) keeps the platform's own variables out of reach, not one organisation's key
out of another's. With two organisations on one instance, B's administrator can declare a
connector whose secret is `env:CONNECTOR_X`, mapped by the operator for A, and the platform uses
A's key for B. With a connector whose host B configures (an MCP server's `url`, within the egress
ceiling), the key leaves for B's host (finding #983).

On the dev tenant the one connector key in the pods' environment was an organisation's own
(Alpha Vantage, choregos-deploy#60): it was moved into the vault on 2026-10-26
(choregos-deploy#109, S39-06e).

## Decision

1. **Writing a reference that is not `store:` needs a platform administrator**
   (`exiger_admin_de_plateforme`: administrator of every organisation, or granted by an edition
   plugin). This holds for organisation and project connectors and for the MCP catalogue install,
   which goes through the same door.
2. **An organisation types its keys.** `secret_values` (or `PUT …/secrets/{field}`) is the way for an
   organisation's administrator and a project owner; the error says so.
3. **A connector holding a deployment reference is the operator's to move.** Re-sending the reference,
   or changing the configuration of an organisation connector that keeps one, needs a platform
   administrator too: with a new URL, the operator's key would follow it. An organisation's
   administrator who types its own value in its place may then edit the connector.
4. **A single-organisation install is unchanged**: its administrator is the platform administrator.

## Consequences

- An operator still maps a key the deployment owns (a shared licence, a platform account) and
  references it; the reference stays readable to the organisation, the value never.
- References written by an organisation's administrator before this decision keep resolving. Listing
  them per organisation, so an operator can review them, is not done here.
- The console's *use a reference instead* still shows for every administrator; for one who is not a
  platform administrator, the API answers 422 with the reason.
