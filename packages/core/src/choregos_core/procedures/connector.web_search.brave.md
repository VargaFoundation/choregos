---
id: connector.web_search.brave
title: Search the web through Brave Search API
applies_to: {connector: web_search/brave}
audience: org_admin
version: 1
reviewed: 2026-10-20
vendor_docs:
  - {title: "Brave Search API: plans and keys", url: "https://api-dashboard.search.brave.com/"}
egress: [api.search.brave.com]
fields:
  api_key: "your Brave Search API API key, typed once"
  price_usd_per_query: "what one query costs under your plan, written to the ledger"
  requests_per_minute: "the organisation's queries per minute to this backend; beyond, the agent gets a 429"
  data_handling: "what your contract grants (retention, reuse, region) with its reference in terms_ref"
  timeout_s: "request timeout in seconds"
---

## Before you start

- A Brave Search API subscription and its key. Read the plan's terms on query retention before you
  fill `data_handling`.
- The built-in `web_search` returns links and snippets, never pages.

## At the vendor

Create a key in the Brave Search API dashboard.

## Permissions

The key only searches. It stays in the API: no runner ever sees it.

## In Choregos

1. Connectors, **Web search**, type **Brave Search API**. `api_key` in the password field (sealed, shown once).
2. Choregos does **not** know what Brave Search API keeps of a query: its terms were not reviewed. Under a
   ceiling that caps retention or forbids reuse, the tool stays off until you fill `data_handling` from
   your contract, for example `{"query_retention_days": 0, "query_reuse": false, "terms_ref": "contract
   ref, section"}`. A declaration can only be stricter, and `terms_ref` is required.
3. The organisation's ceiling opens the tool: Settings, **Policy ceiling**, `web_search: {mode: allow}`.

## Test it

Press **Test**, then start a run: the agent's tool list shows `web_search`. Each query writes a ledger
row `search:brave` and an event with the query's fingerprint, never its text.

## When it fails

- The tool is not offered: the ceiling is closed, the backend is not in its `backends`, or the declared
  data handling does not meet its bounds (an unknown value fails every bound that reads it).
- `query_redacted`: the query held what looks like a secret; nothing was sent.
- `rate_limited`: `requests_per_minute`, or Brave Search API's own quota, is reached; the answer says when to retry.
- `network_policy`: the organisation's network ceiling refuses `api.search.brave.com`.
