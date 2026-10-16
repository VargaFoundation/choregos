---
id: connector.tracker.internal
title: Let Choregos hold the work items
applies_to: {connector: tracker/internal}
audience: project_owner
version: 1
reviewed: 2026-10-16
---

## Before you start

Nothing to create anywhere. Use this when the work does not live in GitHub, Jira or GitLab: requests are
filed in Choregos itself.

## At the vendor

There is no vendor. Work items live in the platform's own database.

## Permissions

Only Choregos roles apply: who may create, move and decide a work item is set by the project's members
and the workflow's guarantees.

## In Choregos

1. Project settings, Connectors, **Tracker**, type **internal**. There is nothing to configure.
2. File work from the board with **New work item**, from the CLI, or through the MCP door with
   `create_work_item`. The platform mints the key.

## Test it

Create a work item on the board and watch it enter its workflow.

## When it fails

- A workflow that reads an external tracker finds nothing here: such work needs a GitHub, Jira or GitLab
  connector instead.
- Work items disappear from view: filter by state and project; they are never filed anywhere else.
