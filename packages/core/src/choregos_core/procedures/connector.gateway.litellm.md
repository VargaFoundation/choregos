---
id: connector.gateway.litellm
title: Connect a LiteLLM gateway
applies_to: {connector: gateway/litellm}
audience: operator
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "LiteLLM virtual keys", url: "https://docs.litellm.ai/docs/proxy/virtual_keys"}
egress: []
fields:
  base_url: "the proxy's address"
  team_id: "the LiteLLM team projects are attributed to"
  internal_prices: "a price list for models the proxy cannot price"
  enterprise_tags: "true only on LiteLLM Enterprise: key tags"
  master_key: "the proxy's master key, typed once"
---

## Before you start

- A LiteLLM proxy and its **master key**. The platform uses it to **mint one virtual key per run**, with
  a hard budget: when the budget is reached LiteLLM refuses, and the run ends `failed (reason=budget)`.
- The usual place for this is the chart: `global.gateway.url` and `global.gateway.secretRef` (key
  `master-key`). A deployment-wide default needs no connector.
- Adding a model for agents to use is done at the gateway; the model catalogue of the administration
  lists what it routes.

## At LiteLLM

Create the models in the proxy's configuration. Optionally create a **team** and note its id so that spend
is attributed to it.

## Permissions

The master key can mint and revoke keys and read spend, so it is the platform's: the runner never holds it
(it gets a per-run key). Tags on keys are a LiteLLM **Enterprise** feature: on the open-source proxy leave
`enterprise_tags` false, or minting fails with `403`.

## In Choregos

Chart values, or Connectors, **Gateway**, type **LiteLLM**: `base_url`, optionally `team_id`,
`internal_prices`, `enterprise_tags`, and `master_key` in the password field.

## Test it

Press **Test**, then open the administration's models list: it shows what the proxy routes.

## When it fails

- `401 LiteLLM Virtual Key expected. Received=<no value>`: the master key never resolved (a missing
  secret renders as the words `<no value>`); the API refuses to start in that case, so fix the secret's
  source.
- `403` when minting keys: `enterprise_tags` is on against an open-source proxy.
- No models listed: the proxy has none, or the key cannot list them.
