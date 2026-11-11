---
title: API overview
description: The REST API's base path, authentication, conventions and resources, and where its OpenAPI document is.
sidebar_position: 4
---

# API overview

Everything the console does goes through the REST API, under `/api/v1` on your instance. The
[REST API guide](../integrate/rest.md) walks through a first script; this page is the map.

## The OpenAPI document

The API is described by an OpenAPI 3 document, the contract the console and the CLI are generated
against:
[`packages/contracts/openapi.yaml`](../../../packages/contracts/openapi.yaml).
Take the version of your instance (`GET /api/v1/edition` says which). The MCP and A2A doors are not
in it: they speak their own protocols ([MCP](../integrate/mcp.md), [A2A](../integrate/a2a.md)).

## Conventions

| | |
| --- | --- |
| Base path | `/api/v1` |
| Authentication | `Authorization: Bearer chg_…`, a token of scope `*` ([REST guide](../integrate/rest.md#1-get-a-full-access-token)); the console uses a session cookie |
| A project in a path | its UUID, `org:slug`, `org/slug`, or a bare slug when it is unique |
| A work item in a path | its UUID or its key (`BIL-12`) |
| Lists | `?cursor=…&limit=…` (1 to 200, 50 by default); the answer is `{"items": [...], "meta": {"next_cursor", "has_more"}}` |
| Errors | RFC 9457 problems ([Error codes](errors.md#the-rest-api)) |
| Concurrent changes | send the version you read (`base_version`, `base_revision`); a `409 stale_base` says someone changed it first |
| Request ids | every answer carries `X-Request-Id` |
| Unknown keys | refused (`422`): a typo does not pass silently |

## Resources

| Area | Main paths |
| --- | --- |
| You and your tokens | `GET /me`, `GET POST /me/tokens`, `DELETE /me/tokens/{id}` |
| Organisations | `GET POST /orgs`, `PATCH /orgs/{org}` |
| Projects | `GET POST /orgs/{org}/projects`, `GET PATCH /projects/{id}`, `GET /projects/{id}/readiness` |
| Applications | `GET POST /projects/{id}/machine-accounts`, `POST /projects/{id}/machine-accounts/{slug}/tokens` |
| Workflows | `GET /projects/{id}/workflows`, `GET PUT /projects/{id}/workflows/{name}`, `POST /projects/{id}/workflows/validate`, `GET PUT /projects/{id}/workflow-routing` |
| Work items | `GET POST /projects/{id}/work-items`, `GET /work-items/{id}`, its `/timeline`, `/artifacts`, `/runs` |
| Decisions | `POST /work-items/{id}/decisions` (a person's session only) |
| Triggers | `GET /projects/{id}/triggers`, `POST /projects/{id}/triggers/{name}/token`, `POST /webhooks/triggers/{org}/{project}/{name}` |
| Runs | `GET /runs/{id}`, `/runs/{id}/events`, `/transcript`, `/logs`, `/diff` |
| Agents | `GET POST /orgs/{org}/agents`, `POST /orgs/{org}/agents/{slug}/versions/publish`, `PUT /projects/{id}/agents/{slug}` (pin) |
| Skills | `GET POST /orgs/{org}/skills`, `POST /orgs/{org}/skills/import`, `POST /orgs/{org}/skills/{slug}/versions` |
| Connectors and tools | `GET POST /orgs/{org}/connectors`, `POST /orgs/{org}/connectors/{name}/discover`, `PATCH /orgs/{org}/connectors/{name}/operations/{operation}` |
| Network | `GET /orgs/{org}/egress`, `POST /orgs/{org}/egress/rules`, `POST /orgs/{org}/egress/requests` |
| Plugins | `GET /orgs/{org}/plugins`, `POST /orgs/{org}/plugins/install` |
| Policy | `GET PUT /projects/{id}/policy`, `PUT /orgs/{org}/policy-ceiling` |
| The instance | `GET /edition` (public: edition and version) |

Paths are relative to `/api/v1`.

## Rate limits

Authenticated REST calls are not rate-limited by the API. Sign-in and webhook routes are limited per
client address (600 a minute by default, `429` with `Retry-After`). The MCP and A2A doors have their
own limits (120 calls a minute per token).
