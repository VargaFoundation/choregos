---
id: connector.documents.sharepoint
title: Connect SharePoint and OneDrive
applies_to: {connector: documents/sharepoint}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Register an application in Microsoft Entra ID", url: "https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app"}
  - {title: "Controlling app access on specific SharePoint site collections", url: "https://devblogs.microsoft.com/microsoft365dev/controlling-app-access-on-specific-sharepoint-site-collections/"}
egress: [graph.microsoft.com, login.microsoftonline.com]
fields:
  tenant_id: "Directory (tenant) ID, from the app's Overview page"
  client_id: "Application (client) ID, from the app's Overview page"
  client_secret: "the secret's Value (not its Secret ID), typed once"
---

## Before you start

- A Microsoft Entra administrator who can **grant admin consent** (Global Administrator or Privileged
  Role Administrator).
- Someone who can **grant the app access to each site**: a SharePoint administrator, or a Graph caller
  holding `Sites.FullControl.All`. Consent alone opens no site (see Permissions).
- The address of each site to read, for example `https://contoso.sharepoint.com/sites/Archi`.
- This is an organisation connector: an organisation administrator declares it once, and knowledge
  sources pick it by name.

## At Microsoft

1. Entra admin center, **App registrations**, **New registration**. Name it for the platform, single
   tenant, no redirect URI.
2. On the new app's Overview page, copy the **Directory (tenant) ID** and the **Application (client) ID**.
3. **Certificates & secrets**, **New client secret**. Copy the secret's **Value** now: it is shown once.
   The Value is what Choregos needs, not the Secret ID. A secret expires (24 months at most): note the
   date in your calendar, because the day it expires every sync of this connector fails.

## Permissions

Least privilege first: the app reads only the sites you name.

1. **API permissions**, **Add a permission**, **Microsoft Graph**, **Application permissions**,
   `Sites.Selected`, then **Grant admin consent**. Never `Files.Read.All` or `Sites.Read.All`.
2. Grant the app **each site**. Find the site's id with
   `GET https://graph.microsoft.com/v1.0/sites/<host>:/sites/<path>` (the answer's `id`), then
   `POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions` with the body
   `{"roles": ["read"], "grantedToIdentities": [{"application": {"id": "<client_id>", "displayName": "Choregos"}}]}`.
   The call must be made by a caller that holds `Sites.FullControl.All`. PnP PowerShell does the same
   with `Grant-PnPAzureADAppSitePermission -AppId <client_id> -DisplayName Choregos -Site <site url> -Permissions Read`.

Without step 2 the app is authorised and reads **nothing**: every site answers 403.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Documents**, type **SharePoint**, any name
   (it is lowercase, and knowledge sources refer to it).
2. `tenant_id`: the Directory (tenant) ID. `client_id`: the Application (client) ID.
3. `client_secret`: paste the secret's Value in the password field. It is sealed and never shown again;
   to rotate it, use **Replace**.
4. Leave `graph_url` and `login_url` alone unless you use a sovereign cloud.
5. Then **Knowledge**, a base, **Add source**, **SharePoint**: pick this connector, the site (or a
   OneDrive's drive) and, if you want, a folder.

## Test it

This connector has no test button of its own. Add the source, then press **Sync now**: the source shows
what it added and, if it failed, its last error. A first sync that lists documents proves the secret,
the consent and the site grant at once.

## When it fails

- `AADSTS7000215` (invalid client secret): the Secret **ID** was pasted instead of the **Value**.
- `AADSTS700016` (application not found): wrong `client_id`, or wrong `tenant_id`.
- `403 accessDenied` on a site: the site grant (Permissions, step 2) is missing for that site.
- `401` months after it worked: the client secret expired. Create a new one and **Replace** it.
- A timeout: the API cannot reach `graph.microsoft.com` or `login.microsoftonline.com`; the
  network egress allowlist must open both.
