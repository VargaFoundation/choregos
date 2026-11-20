# 0198 — The Azure DevOps family: Boards first, then Repos and Pipelines, one connection model

- **Status**: accepted, 2026-11-20 (the project lead's request of the same day; written by story S13-07,
  which delivers point 1 to 4; S13-08 delivers point 5 and amends this page)
- **Concerns**: `packages/adapters` (`azure_devops.py`, `tracker/azure_boards*.py`, `tracker/azure_html.py`,
  `fakes/azure_boards.py`, `specs.py`, `builtins.py`), `apps/api/src/tranibor_api/routers/webhooks.py`,
  `docs/procedures` (`connector.tracker.azure-boards`), `docs/plan/BLOCKERS.md` (the frozen S13-05 note)

## Context

S13-05 delivered the Azure Container Apps executor and left the rest of the Azure DevOps line blocked: no
reachable organisation, so no Boards tracker, no Repos, no Pipelines. The gap is real for any operator whose
work is tracked in Azure Boards: a work item cannot start a workflow, and a workflow cannot write its state
back. The platform already has the pattern (the Jira tracker: read, states, comments, custom fields, labels,
links, inbound webhooks), and a Microsoft Entra client-credentials exchange (`identity/entra.py`, scoped to
Graph). Azure DevOps Services and Azure DevOps Server 2022 expose the same REST API 7.1, so one family of
connectors can serve both; they differ in the credential and in the address.

## Decision

1. **One family, one connection model.** The connectors of the Azure DevOps family (this story's tracker
   `tracker/azure-boards`, and S13-08's `scm/azure-repos` and `ci/azure-pipelines`) share: `base_url`, the
   *collection*, never a hard-coded `dev.azure.com` (`https://dev.azure.com/<organisation>` on the Services,
   `https://<server>/<virtual directory>/<collection>` on a Server 2022); `project`, the team project;
   and two credentials, selected by `auth`: `pat` (a personal access token, sent as HTTP Basic with an empty
   user name, the only credential a Server accepts) or `entra` (a Microsoft Entra service principal,
   `tenant_id` + `client_id` + the sealed `client_secret`, Services only). Secrets are fields of the
   connector, sealed, never a key of `config`. One client class (`AzureDevOpsClient`) puts the `api-version`
   (7.1) on every call and the credential on every request; the other connectors of the family reuse it.
2. **One Entra exchange, one client module.** `identity/entra.py` fetches Graph tokens only (its scope is
   its own). Azure DevOps has `EntraClientCredentials`, which asks the same v2.0 token endpoint with the same
   grant for the Azure DevOps resource (`499b84ac-1321-427f-aa17-267ca6975798/.default`), caches the token
   until a minute before it expires, and refuses nothing silently: a refused principal raises an
   `UpstreamError` naming the endpoint and the connector's own service (`azure-boards`, `azure-repos` or
   `azure-pipelines`). It lives, with `AzureDevOpsClient`, `azure_client` (the one builder of the client from
   a connector's configuration), `check_azure_devops` (the one configuration check, wrapped by
   `check_azure_boards`, `check_azure_repos` and `check_azure_pipelines`), `azure_egress` and
   `shared_secret_of`, in **`tranibor_adapters/azure_devops.py`**. S13-07 and S13-08 were built in parallel
   and first carried two copies; they were folded into this module (S13-08 follow-up), and
   `tests/test_azure_one_client.py` fails if a second client class, token class or secret reader returns.
   The folded client takes, from the two copies, the better of each piece: the `203` sign-in page of a
   refused PAT becomes a named `UpstreamError` for Boards too; the Entra login host of the egress follows the
   connector's `login_url`; the shared `RestClient` supplies the retry on 429/500/502/503/504 (`Retry-After`
   honoured, 30 s cap) and never follows a redirect, so a credential is not replayed elsewhere. `auth: entra`
   on a host that is not `dev.azure.com` or `*.visualstudio.com` is refused when the connector is saved (a
   Server has no Entra sign-in for an application). Boards' egress stays without `vssps.dev.azure.com`,
   which only Repos calls.
3. **The Boards tracker implements Jira's capabilities with Azure's mechanics.** Reads: a WIQL query
   (`POST wit/wiql`) within the team project, the optional `area_path` (`UNDER`), the `agent-ready` tag or
   the connector's `candidates_wiql` or a start rule, never a work item whose state category is Completed or
   Removed; the item itself with `$expand=all`. A **state is written**, not transitioned: `System.State`,
   after reading the states of the item's type; designated by exact name, by category (`category:Proposed`,
   `InProgress`, `Resolved`, `Completed`, `Removed`, or Jira's `new`/`indeterminate`/`done`), or by the
   canonical `To Do`/`In Progress`/`Done`; two states in one category are an error that names them; a state
   already reached is not written again (ADR 0008). Labels are **tags** (`System.Tags`, `;`-separated). The
   structured fields (`Cost (€)`, `Size`, `Risk`, `Run`, overridable by `field_names`, with the former French
   names honoured as for Jira) are resolved by display name to their reference name from `wit/fields`, once;
   a field the process lacks is skipped. Descriptions and comments are HTML, rendered from Markdown with
   everything escaped (a link is `http(s)` or text). Comments use the only version the API offers,
   `7.1-preview.4`. Links are real relations (`Related`, `Duplicate-Forward`, `Dependency-Forward`); an
   existing relation is not an error. `fields.tracker` carries the creator, the tags and the `Custom.*`
   answers by display name, as for Jira.
4. **Inbound: one route, `/api/v1/webhooks/azure-boards`, for the Service Hooks** (`workitem.created`,
   `workitem.updated`, `workitem.commented`, `workitem.deleted`). A service hook does not sign: the only
   barrier is the platform's shared secret (`TRANIBOR_GENERIC_WEBHOOK_SECRET`), accepted as the **password of
   the subscription's basic authentication** (any user name) or in `X-Choregos-Secret`, compared in constant
   time; none configured, none accepted. A delivery is routed to the project whose Azure Boards connector
   holds its team project (case-blind), then, among several, by the organisation address (compared whole,
   because on the Services the organisation is in the path, not the host), by `area_path`, by `match_labels`;
   an ambiguity routes nothing, as for Jira. Without a connector the lower-cased team project name is the
   slug, as Jira's key prefix is. The delivery id is the hook's `notificationId`, so a redelivery starts
   nothing twice. A tracker move carries a `status_category` only for the state names the four standard
   processes share; a team's own state carries none, and an item that ended is then never brought back by
   such a move (the safe side). Adding the route is a `contract(openapi)` change (a separate commit).
5. **Repos and Pipelines (S13-08)** add `scm/azure-repos` and `ci/azure-pipelines` on the same
   `AzureDevOpsClient`, the same two credentials and the `project` of point 1, with the service hooks for
   pull requests and builds on a route of their own, `/api/v1/webhooks/azure-devops`. The two routes stay
   separate: Boards hooks are routed by team project, area path and tags to a tracker, Repos and Pipelines
   hooks by team project and repository to an SCM or CI connector, and merging them would be a contract
   change with no gain. Both read the shared secret with the one `shared_secret_of`.

## Consequences

- An operator whose board is Azure Boards can use it as Jira is used: tag an item `agent-ready` (or set a
  WIQL), the workflow starts, its state, comment, fields and links go back under the dedicated account.
- Everything above is proven against a scripted in-memory server written from Microsoft's documentation, and
  service-hook payloads written from its events reference. **No Azure DevOps organisation or Server was
  reachable**: the live proof (S13-04's way: a sandbox project, a complete cycle) is still owed, and the
  story's status row says 🟡 until it is run. The points most likely to differ from a real organisation are
  the shape of `workitem.commented` payloads, the `Duplicate-Forward` direction, and the preview comments API.
- The console shows the type under its raw name and a generic account line until the web gets the type's
  display name (a follow-up; the connector form itself is drawn from the spec).
- A webhook route and an OpenAPI path are added; the layering and route guards are unchanged (the route is
  covered by `/webhooks/*`).
