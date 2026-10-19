---
id: connector.identity.entra
title: Connect Microsoft Entra ID for joiners and leavers
applies_to: {connector: identity/entra}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Register an application in Microsoft Entra ID", url: "https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app"}
  - {title: "Assign Microsoft Entra roles with administrative unit scope", url: "https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/admin-units-assign-roles"}
  - {title: "Microsoft Graph permissions reference", url: "https://learn.microsoft.com/en-us/graph/permissions-reference"}
egress: [graph.microsoft.com, login.microsoftonline.com]
fields:
  tenant_id: "Directory (tenant) ID"
  client_id: "Application (client) ID"
  client_secret: "the secret's Value, typed once"
  administrative_unit_id: "the Object ID of the administrative unit the platform may touch"
---

## Before you start

- An Entra administrator (Global Administrator or Privileged Role Administrator) who can grant consent
  and assign directory roles.
- An **administrative unit** that holds only the accounts the platform may manage. A
  restricted-management administrative unit is recommended.
- This connector writes to the directory: its operations (`create_user`, `add_to_group`,
  `remove_from_group`, `disable_user`, `revoke_sessions`) are governed actions that wait for a human
  approval, and `get_user` reads.

## At Microsoft

1. App registrations, **New registration** (single tenant, no redirect URI). Copy the **Directory
   (tenant) ID** and the **Application (client) ID**.
2. **Certificates & secrets**, **New client secret**: copy the **Value**, shown once. Note its expiry.
3. Create the administrative unit (Entra ID, Roles & admins, Administrative units) and copy its
   **Object ID**.

## Permissions

Preferred, because it is bounded: give the app's service principal the **User Administrator** and
**Groups Administrator** roles **scoped to the administrative unit** (Roles and administrators, open the
role, **Add assignments**, choose the administrative unit scope, select the app).

Fallback: Microsoft Graph **application** permissions `User.ReadWrite.All` and `GroupMember.ReadWrite.All`
with admin consent. They are **tenant-wide**: the connector refuses to touch an account outside
`administrative_unit_id`, and that check is then the only bound. Say so when you ask for approval.

Not proven here: the role-based variant has not been run against a live tenant from this repository.
Check the first run on a test unit before pointing it at people.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Identity**, type **Microsoft Entra ID**.
2. `tenant_id`, `client_id`: from the app's Overview. `client_secret`: the Value, in the password field.
3. `administrative_unit_id`: the unit's Object ID. Every account the platform creates enters it; any
   other account is refused.

## Test it

Run a workflow step (or ask through the MCP door) that calls `get_user` on a known user principal name
inside the unit. A returned profile proves the secret and the read permission.

## When it fails

- `AADSTS7000215`: the Secret ID was pasted instead of the Value.
- `AADSTS700016`: wrong `client_id` or `tenant_id`.
- `403 Authorization_RequestDenied`: the role or permission is missing, or the target account is not in
  the administrative unit.
- A refusal naming the administrative unit: the account is outside `administrative_unit_id`, by design.
- A timeout: open `graph.microsoft.com` and `login.microsoftonline.com` in the egress allowlist.
