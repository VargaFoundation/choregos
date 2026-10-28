---
id: connector.mcp.hello
title: Connect the hello server
applies_to: {connector: mcp/hello}
audience: org_admin
version: 1
reviewed: 2026-10-27
egress: []
---

## Before you start

Install the `hello` plugin. Its server runs in the platform; there is nothing to host.

## At the vendor

There is no vendor and nothing to create.

## Permissions

The server only answers greetings; it reads and writes nothing.

## In Choregos

Catalogue, **Plugins**, `hello`. Fill in the greeting you want.

## Test it

Press **Test** on the connector: the `say_hello` tool answers.

## When it fails

- The connector stays unreachable: check that the plugin server is running.
