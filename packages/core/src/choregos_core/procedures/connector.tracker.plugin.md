---
id: connector.tracker.plugin
title: Use a plugin's server as the board
applies_to: {connector: tracker/plugin}
audience: project_owner
version: 1
reviewed: 2026-10-28
vendor_docs:
  - {title: "Writing a plugin: the tracker profile", url: "https://github.com/VargaFoundation/choregos/blob/main/docs/plugins.md"}
egress: []
values: []
fields:
  plugin: "the name of an installed plugin whose server declares `implements: [tracker]`"
  server: "the name of that server, as listed under the plugin in Catalogue, Plugins"
---

## Before you start

- A plugin installed in the organisation whose server is a tracker (Catalogue, **Plugins**: the server
  says `tracker`). The plugin's own procedure says how to reach its system; follow it first.
- The server **configured and running** (status `deployed`). A server that is installed but not
  configured runs no replica, and a project cannot use it.

## At the vendor

Nothing here: the plugin's own procedure covers its system — the account, its rights, and the webhook
the system posts to.

## Permissions

The account the plugin's server uses is the one its procedure asks for. Choregos itself needs nothing
more: it reaches the server inside the cluster with the server's own token, never with a key of yours.

## In Choregos

1. Project settings, Connectors, **Tracker**, type **Plugin server**.
2. `plugin`: the plugin's name. `server`: its server's name.
3. Nothing else: where the server answers and its token are read from the organisation's connector each
   time, never typed nor stored on the project.

A project created from a plugin's template whose tracker is `plugin:<plugin>/<server>` gets this
connector by itself.

## Test it

Press **Test** on the connector: it asks the server for its candidate items. Then create an item in the
system the plugin connects and watch it appear on the board.

## When it fails

- `422` when saving: the plugin is not installed in the organisation, or its server is not `deployed` —
  configure the server first.
- `404` on the webhook: the URL must name this organisation, the plugin and the server
  (`/api/v1/webhooks/plugins/<org>/<plugin>/<server>`), and the server must declare a `webhook`.
- `401` on the webhook: the server did not recognise the delivery — its own secret differs from the one
  the system sends. See the plugin's procedure.
- Nothing appears on the board: the item belongs to no project of the organisation on this tracker, or
  the organisation has several such projects and none holds the item yet.
