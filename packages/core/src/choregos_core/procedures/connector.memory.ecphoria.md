---
id: connector.memory.ecphoria
title: Connect Ecphoria, the platform's memory
applies_to: {connector: memory/ecphoria}
audience: operator
version: 1
reviewed: 2026-10-16
egress: []
fields:
  base_url: "Ecphoria's address, for example http://ecphoria.<namespace>.svc:8432"
  tenant: "the Ecphoria tenant a project writes to"
  read_timeout_ms: "how long a read may take before the run goes on without memory"
  token: "Ecphoria's token, typed once"
---

## Before you start

- A running Ecphoria. The chart can deploy one (`global.memory.embedded: true`) or point at yours
  (`global.memory.url`, `global.memory.tokenSecret`).
- Memory is optional: without it the platform falls back to the `lexical` memory, with no service.
- Reads have a short timeout on purpose (300 ms by default): a slow memory never blocks a stage, it gives
  an empty context pack. Writes are governed: the orchestrator writes, the agent proposes.

## At Ecphoria

Create or choose the **tenant** each project writes to, and a token if Ecphoria requires one. Ecphoria's
own documentation is the reference for that; this repository does not describe it.

## Permissions

The token must be able to read and write memories of the tenant and create tenants if the platform is to
create one per project.

## In Choregos

Usually the chart's `global.memory.*` values. To declare it on a project: Connectors, **Memory**, type
**Ecphoria**, `base_url`, `tenant`, optionally `read_timeout_ms`, and `token` in the password field.

## Test it

Press **Test**: it calls Ecphoria's `/health` for the tenant and shows its status.

## When it fails

- `ok: false` with an error: the address is unreachable from the API, or the token is refused.
- Runs start with an empty memory: the read hit the timeout; raise `read_timeout_ms` if Ecphoria is
  healthy but slow.
- `401` on writes: the token lacks write rights.
