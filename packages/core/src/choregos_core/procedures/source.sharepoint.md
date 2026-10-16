---
id: source.sharepoint
title: Add a SharePoint or OneDrive source to a knowledge base
applies_to: {source: sharepoint}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Controlling app access on specific SharePoint site collections", url: "https://devblogs.microsoft.com/microsoft365dev/controlling-app-access-on-specific-sharepoint-site-collections/"}
egress: [graph.microsoft.com, login.microsoftonline.com]
fields:
  connector: "the organisation's documents/sharepoint connector, picked from a list"
  site_id: "the SharePoint site's Graph id"
  drive_id: "a OneDrive's or a library's drive id, instead of a site"
  folder: "a folder under the drive, to read only a branch"
---

## Before you start

- A **SharePoint connector** declared by an organisation administrator (see "Connect SharePoint and
  OneDrive"): it holds the Entra app and its secret. The source form picks it from a list and never asks
  for a secret.
- The app must have been **granted the site** (`Sites.Selected` plus a per-site grant). Consent alone grants
  none, and every sync then fails with 403.
- The site's Graph id, or the id of the drive.

## At Microsoft

Find the site's id with `GET https://graph.microsoft.com/v1.0/sites/<host>:/sites/<path>` (the answer's
`id`), or a drive's id with `GET /v1.0/sites/{site-id}/drives`. The connector's procedure shows how to
grant the app the site.

## Permissions

The source reads what the app was granted on that site, with delta sync. Everything it reads becomes
readable by everyone the base is open to, whatever the document's own permissions say.

## In Choregos

1. **Knowledge**, the base, **Add source**, kind **SharePoint**.
2. Pick the **connector**. With none in the list, the form links to Administration, Connectors, to declare
   one.
3. `site_id` (or `drive_id` for a OneDrive), and optionally a `folder`.
4. Choose the refresh: every N minutes (5 minutes to 7 days), a cron expression, or manual.

## Test it

Press **Sync now** on the source: it shows added, updated, deleted and skipped documents, and the last
error if there is one.

## When it fails

- "no `documents` connector": no administrator has declared a SharePoint connector yet.
- `403 accessDenied`: the app was not granted that site.
- A wrong connector name: names are lowercase, and the connector must be of type SharePoint (the message
  suggests the right one).
- Documents missing: the app was granted another site, or the folder does not exist.
