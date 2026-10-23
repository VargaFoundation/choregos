---
id: connector.gateway.openai-compatible
title: Connect an OpenAI-compatible gateway
applies_to: {connector: gateway/openai-compatible}
audience: operator
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "OpenAI API: list models", url: "https://platform.openai.com/docs/api-reference/models/list"}
egress: ["the gateway's host"]
fields:
  base_url: "the gateway's address, ending where /models and /chat/completions hang"
  headers: "extra headers the gateway requires"
  timeout_s: "request timeout in seconds"
  api_key: "the key used to discover models, typed once"
  run_key: "the key every run receives, typed once"
---

## Before you start

- Any gateway that serves `GET {base_url}/models` and `/chat/completions` (Portkey, Kong AI Gateway, Envoy
  AI Gateway, OpenRouter, a vLLM router...).
- Read the limits first. This adapter gives **discovery only**: no per-run virtual key, no hard budget at
  the source, no measured cost (spend is "not measured", not "free"). Choregos alone holds the budget
  (the reservation, turns and minutes).

## At the gateway

Create two credentials: one for **discovery** (`api_key`), and one that **runs** will use (`run_key`). Every
run receives the same `run_key`, so scope it at the gateway: a consumer of its own, with its own token rate
limit.

## Permissions

`api_key` only lists models. `run_key` can call the models you allow: that is where the damage is bounded,
so bound it there.

## In Choregos

Chart (`global.gateway.type: openai-compatible`) or Connectors, **Gateway**: `base_url`, optional `headers`
and `timeout_s`, then `api_key` and `run_key` in their password fields.

With `sandbox.llm_security_analyzer` in the project's policy (the `team` and `regulated` presets), this
gateway is refused: it has no guard to mask secrets on the LLM path. Use the LiteLLM gateway,
or turn the flag off in the policy.

## Test it

Open the administration's models list: it shows what `GET {base_url}/models` returns.

## When it fails

- An empty models list: the `api_key` cannot list, or `base_url` is wrong (it must be the base of the API).
- `401`: wrong key, or a header the gateway needs is missing (`headers`).
- Spend always shows nothing: expected with this adapter.
