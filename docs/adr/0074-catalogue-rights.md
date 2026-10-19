# 0074 — Catalogue rights: who sees, installs, enables and uses an MCP server or a connector

- **Status**: accepted, 2026-10-17
- **Concerns**: `services/catalogue_rights.py`, `services/connector_catalogue.py`, the routes
  `GET /orgs/{org}/mcp-catalogue`, `GET /orgs/{org}/connector-catalogue` and
  `/projects/{id}/org-connectors`, the broker (`services/courtier.py`), the project policy
  (`services/connecteurs.py`), the tables `org_connectors.availability` and `org_connector_projects`;
  amends [ADR 0034](0034-connectors-by-capability.md) (section 4, who changes groups; section 6, the
  intersection gains a term), [ADR 0065](0065-catalogues-of-mcp-servers-and-skills.md) (section 4,
  "available is not enabled" gains a per-project step) and
  [ADR 0014](0014-a-platform-held-tool-catalogue.md) (Decision 2.3: for an organisation connector the
  project's own lock is its enablement); builds on [ADR 0061](0061-agent-ownership-scopes-and-forks.md)
  (the server decides `can`)

## Context

- The MCP catalogue sat inside the administration and needed `project:read` at organisation level: a
  person holding only a project role got `403`. Connector types had no catalogue at all.
- The console showed an enabled Install button to everyone; a member clicked and met the API's `403`.
- An installed organisation connector was reachable by every project whose groups matched. ADR 0014
  promised two locks, "the deployment says who is entitled, the project says what it uses", but the
  project's lock was `config.groups`, which only an administrator sets (`tools:grant`): the project
  chose nothing.

## Decision

One server function decides, `decide(principal, org, action, project_slug=None)`; routers call it and
DTOs carry its answers as `can` and `cannot_reason`. The console never guesses a right from a role.

| Gesture | Organisation admin | Project owner of P | Member (any role) |
| :-- | :-- | :-- | :-- |
| See the catalogue and which instances exist | yes | yes | yes |
| Install an organisation-scope entry | yes | no | no |
| Configure a project-scope type (tracker, scm, ci, cd, notify) in P | yes | yes, in P | no |
| Set an organisation connector's availability, policies, groups | yes | no | no |
| Enable or disable an organisation connector for P | yes, any project | yes, in P | no |
| Use: a run of P calls an operation | enabled for P, effective policy not forbidden, groups match, the agent version selects it | | |

1. **Member** means any role in the organisation or in one of its projects (`agent_governance.is_member`).
2. **Availability** per organisation connector: `all_projects` (the server default, the behaviour
   before this ADR: the migration changes nothing) or `selected_projects` (opt-in rows in
   `org_connector_projects`). The API default stays `all_projects`; the console proposes
   `selected_projects`.
3. **Use is enforced where a project reaches an organisation connector**: the broker
   (`outils_du_courtier`), the project policy (`politique_pour_le_projet`, behind governed actions and
   the `connector.call` effect) and `GET /projects/{id}/operations`.
4. **Seeing is not secret**: field names and states, never values. `enabled_projects` lists only the
   projects the caller can see.
5. Switching `all_projects` to `selected_projects` enables the connector for no project; switching
   back deletes nothing, so a later switch finds the rows again.
6. **Egress** of an installed MCP server is not decided here; the install route and
   `declare_org_connector` are the shared seam with network policies.

## Consequences

- An `all_projects` connector behaves as before; a `selected_projects` one is opt-in per project.
- Enabling is audited (`org.connector.enable`, `org.connector.disable`, `org.connector.availability`).
- Not covered: knowledge sources that read an organisation `documents` connector are read by the
  platform, not by an agent, and are not gated by enablement.

## Alternatives rejected

- Per-operation `groups` only: an entitlement the administrator sets, not a project's opt-in.
- Letting project owners edit `config.groups`: it is the entitlement lock; giving it away removes the first lock.
- Backfilling enablement rows for every project: N by M rows, and a new project would silently miss a connector.
- Hiding entries from non-administrators: they could not ask for a server they cannot see.
- A catalogue built in the console from `GET /connectors/types`: no organisation state, no rights.
