---
id: connector.tracker.azure-boards
title: Use Azure DevOps Boards as the board
applies_to: {connector: tracker/azure-boards}
audience: project_owner
version: 1
reviewed: 2026-11-20
vendor_docs:
  - {title: "Use personal access tokens", url: "https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate"}
  - {title: "Use service principals and managed identities in Azure DevOps", url: "https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/service-principal-managed-identity"}
  - {title: "Service hooks events (work item created, updated, commented)", url: "https://learn.microsoft.com/en-us/azure/devops/service-hooks/events"}
  - {title: "Create a web hooks subscription", url: "https://learn.microsoft.com/en-us/azure/devops/service-hooks/services/webhooks"}
egress: ["dev.azure.com, or your Azure DevOps Server host", "login.microsoftonline.com (service principal)"]
values: ["webhook_url:azure-boards"]
fields:
  base_url: "https://dev.azure.com/<organisation>, or the collection of an Azure DevOps Server 2022 (https://<server>/<path>/<collection>)"
  project: "the Azure DevOps team project"
  area_path: "optional: only the work items under this area path (Project\\Team)"
  auth: "`pat` (the default) for a personal access token; `entra` for a Microsoft Entra service principal, Azure DevOps Services only"
  tenant_id: "`entra` only: the Entra tenant (directory) id"
  client_id: "`entra` only: the service principal's application (client) id"
  candidates_wiql: "optional: WIQL conditions of the work items the platform takes on by itself, without a service hook"
  match_labels: "optional: when several projects share this team project, the tags that send a work item here; the project without them takes the rest"
  field_names: "a map of the custom fields Tranibor writes (Cost, Size, Risk, Run) to their names in your process"
  pat: "the personal access token, typed once (`auth: pat`)"
  client_secret: "the service principal's client secret, typed once (`auth: entra`)"
  webhook_secret: "not used by the service-hook endpoint: the platform's shared webhook secret is"
---

## Before you start

- An Azure DevOps organisation (Azure DevOps Services) or an Azure DevOps Server 2022 collection, and
  an account **dedicated to Tranibor**: its permissions are what the platform may do in the project,
  and comments and state changes appear under its name.
- The team project's name, and the area path if Tranibor should see only one team's work.
- The platform's shared webhook secret (`TRANIBOR_GENERIC_WEBHOOK_SECRET`), held by whoever operates
  Tranibor. A service hook does not sign its deliveries, so the platform accepts one only if it carries
  this secret: as the **password** of the subscription's basic authentication (any user name), or in an
  `X-Choregos-Secret` header.

## At Azure DevOps

1. **The credential.**
   - A **personal access token** (works on the Services and on Server): the dedicated account, **User
     settings**, **Personal access tokens**, **New token**. Scopes: **Work Items: Read & write** and
     **Project and Team: Read**, nothing else. Pick the longest expiry offered and **note the date**.
   - A **Microsoft Entra service principal** (Services only, no licence-bound user, no token to rotate by
     hand): register an application in Entra, create a client secret, then add the service principal
     to the organisation (**Organization settings**, **Users**, **Add users**) with the *Basic* access
     level, and to the project's **Contributors**. Tranibor asks Entra for a token for the Azure DevOps
     resource with the client credentials grant.
2. **The project.** Add the account (or the service principal) to the project with a group that holds the
   permissions below. **A token's scopes give no right on a project by themselves.**
3. **Service hook.** **Project settings**, **Service hooks**, **Create subscription**, **Web Hooks**.
   Create one subscription for each of **Work item created**, **Work item updated** and **Work item
   commented on**, filtered on the area path if you set one. URL: `<api_url>/api/v1/webhooks/azure-boards`.
   Basic authentication user name: anything; password: the platform's shared webhook secret. Leave
   *Resource details to send* on **All**. This path has not been exercised against a live organisation
   from this repository.

## Permissions

On the project, for the dedicated account: **View work items in this node**, **Edit work items in this
node**, **Create work items** (if Tranibor files items), **View project-level information**.

**What Tranibor writes, and as whom.** The workflow's bookkeeping goes through this connector, through
the REST API 7.1, under the dedicated account: the state, the tags, the status comment, the `Cost (€)`,
`Size`, `Risk` and `Run` fields, the questions to a person, the close-out. The state is written as it is
(Azure Boards has no transitions to look for): Tranibor first reads the states of the item's type and
names the available ones in the error if the workflow's state is not among them. A state can be
designated by its exact name, by its category (`category:Proposed`, `category:InProgress`,
`category:Resolved`, `category:Completed`) or by the canonical `To Do`, `In Progress`, `Done`.

The custom fields must exist in your process (an inherited process: **Organization settings**,
**Process**, add fields `Cost (€)`, `Size`, `Risk`, `Run`). A field the project does not have is skipped,
never a failure.

## In Tranibor

1. Project settings, Connectors, **Tracker**, type **Azure DevOps Boards**.
2. `base_url`: `https://dev.azure.com/<organisation>`, or the collection URL of a Server. `project`: the
   team project. `area_path`: optional. `auth`: `pat` (the default) or `entra`; with `entra`, `tenant_id`
   and `client_id` too.
3. `pat`, or `client_secret`: the secret, in the password field (sealed, shown once).
4. `field_names`: only if your process names the custom fields differently. They are `Cost (€)`, `Size`,
   `Risk` and `Run`.
5. Tag a work item `agent-ready`: the platform starts one workflow per work item. A delivery reaches
   the project whose Azure Boards connector holds the item's team project, whatever the project's slug;
   two projects on one team project are told apart by the organisation, then `area_path`, then `match_labels`.
6. Or skip the service hook and the tag: set `candidates_wiql`, for example
   `[System.Parent] = 1234 AND [System.Tags] CONTAINS 'form-a'`. Every minute the platform runs it within
   the team project, among the items not Completed or Removed, and starts one workflow per new item.
7. Nothing to set for what the agents learn about an item: when the platform takes one on it keeps, as
   `fields.tracker`, the creator, the tags and the **answers**: every non-empty custom field (`Custom.*`),
   keyed by its display name, as text, each cut at 2000 characters. It is the requester's text, never an
   instruction.

## Test it

Press **Test** on the connector. It reads the project with the credential, which proves the token (or the
service principal), its scopes and its place in the project at once. Then tag a work item `agent-ready`
and watch it appear on the board.

## When it fails

- `401`: an expired or revoked token, or a service principal that is not a member of the organisation.
- `403`: the token lacks **Work Items: Read & write** or **Project and Team: Read**, or the account lacks
  a project permission above.
- `404` on the project: wrong `project`, or the account is not in the project.
- `auth: entra` refused on save: Azure DevOps Server has no Entra sign-in for an application, use `pat`.
- `no state of the type ... is "X"`: the workflow names a state your process does not have; the error lists
  the ones it has. Several states in one category: name the one you want.
- A service hook answered `401 invalid shared secret`: the subscription's password differs from the
  platform's shared secret.
- Nothing starts after tagging: the tag must be exactly `agent-ready`, and the hook must reach this platform.
- A timeout: the egress allowlist must open `dev.azure.com` (or your Server) and, for a service principal,
  `login.microsoftonline.com`.
