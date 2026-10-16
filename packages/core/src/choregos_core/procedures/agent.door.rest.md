---
id: agent.door.rest
title: Use the Choregos REST API
applies_to: {door_client: rest}
audience: everyone
version: 1
reviewed: 2026-10-16
link: /integrations/rest
values: [api_url]
egress: []
---

## Before you start

The REST API at `{{api_url}}` is for your own programs; its OpenAPI document describes every route. It is
listed with the door's clients because it is set up the same way, but it does not go through the MCP door.

## At the client

Nothing to install. Any HTTP client works. The Integrations page of this console has a `curl` example
with this platform's address filled in; open it with the link below the panel.

## Permissions

A **full-access** token (scope `*`) that you mint yourself, sent as `Authorization: Bearer <token>`. A
token with an MCP scope is refused here. The API applies **your** rights to every call.

## In Choregos

Mint a token from Integrations, store it where your program keeps secrets, and give it an expiry.

## Test it

`curl -H "Authorization: Bearer <token>" {{api_url}}/api/v1/me` should answer who you are.

## When it fails

- `401`: the token is missing, revoked or expired.
- `403`: an MCP-scoped token was used, or your role lacks the right for that route.
