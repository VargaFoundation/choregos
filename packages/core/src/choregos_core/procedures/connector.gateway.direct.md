---
id: connector.gateway.direct
title: Use the direct gateway (development only)
applies_to: {connector: gateway/direct}
audience: operator
version: 1
reviewed: 2026-10-16
egress: ["the provider's host"]
fields:
  models: "the model names the key may use"
  key: "the provider key, typed once"
---

## Before you start

- **Development only.** This gateway hands out the key it was given: it mints no key per run, so there is
  no hard cap, and the cost ledger stays at zero. On staging and prod Choregos **refuses to build it** and
  says so: use `litellm` (or `openai-compatible`, knowing its limits).

## At the vendor

Create a provider API key with a spending limit set at the provider's side.

## Permissions

The key is used as is by every run. Give it only the models you list.

## In Choregos

Connectors, **Gateway**, type **direct**: `models` (a list of names), `key` in the password field.

## Test it

Run a stage on a development project.

## When it fails

- A refusal that names `gateway: direct` in staging or prod: by design; change the gateway type.
- A cost always at zero: expected, nothing is measured.
