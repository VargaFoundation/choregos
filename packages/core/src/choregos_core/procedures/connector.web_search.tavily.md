---
id: connector.web_search.tavily
title: Search the web through Tavily
applies_to: {connector: web_search/tavily}
audience: org_admin
version: 1
reviewed: 2026-10-20
vendor_docs:
  - {title: "Tavily: API keys", url: "https://app.tavily.com/"}
egress: [api.tavily.com]
fields:
  api_key: "your Tavily API key, typed once"
  price_usd_per_query: "what one query costs under your plan, written to the ledger"
  requests_per_minute: "the organisation's queries per minute to this backend; beyond, the agent gets a 429"
  data_handling: "what your contract grants (retention, reuse, region) with its reference in terms_ref"
  timeout_s: "request timeout in seconds"
---

## Before you start

- A Tavily account and its API key. Choregos never asks Tavily for page contents: reading a result
  goes through `web_fetch`.
- The built-in `web_search` returns links and snippets, never pages.

## At the vendor

Create a key in the Tavily dashboard.

## Permissions

The key only searches. It stays in the API: no runner ever sees it.

## In Choregos

1. Connectors, **Web search**, type **Tavily**. `api_key` in the password field (sealed, shown once).
2. Choregos does **not** know what Tavily keeps of a query: its terms were not reviewed. Under a
   ceiling that caps retention or forbids reuse, the tool stays off until you fill `data_handling` from
   your contract, for example `{"query_retention_days": 0, "query_reuse": false, "terms_ref": "contract
   ref, section"}`. A declaration can only be stricter, and `terms_ref` is required.
3. The organisation's ceiling opens the tool: Settings, **Policy ceiling**, `web_search: {mode: allow}`.

## Test it

Press **Test**, then start a run: the agent's tool list shows `web_search`. Each query writes a ledger
row `search:tavily` and an event with the query's fingerprint, never its text.

## When it fails

- The tool is not offered: the ceiling is closed, the backend is not in its `backends`, or the declared
  data handling does not meet its bounds (an unknown value fails every bound that reads it).
- `query_redacted`: the query held what looks like a secret; nothing was sent.
- `rate_limited`: `requests_per_minute`, or Tavily's own quota, is reached; the answer says when to retry.
- `network_policy`: the organisation's network ceiling refuses `api.tavily.com`.
