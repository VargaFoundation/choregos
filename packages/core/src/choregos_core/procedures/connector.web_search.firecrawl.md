---
id: connector.web_search.firecrawl
title: Search the web through a self-hosted Firecrawl
applies_to: {connector: web_search/firecrawl}
audience: org_admin
version: 2
reviewed: 2026-10-23
vendor_docs:
  - {title: "Firecrawl: self-hosting", url: "https://docs.firecrawl.dev/contributing/self-host"}
  - {title: "Firecrawl: search endpoint", url: "https://docs.firecrawl.dev/features/search"}
egress: ["your self-hosted Firecrawl's host"]
fields:
  base_url: "the address of your self-hosted Firecrawl instance, required; Firecrawl's cloud (firecrawl.dev) is refused"
  api_key: "the key your instance expects, typed once (optional: a self-hosted instance may run without one)"
  price_usd_per_query: "what one query costs you, written to the ledger"
  requests_per_minute: "the organisation's queries per minute to this backend; beyond, the agent gets a 429"
  data_handling: "what your contract grants (retention, reuse, region) with its reference in terms_ref"
  timeout_s: "request timeout in seconds"
---

## Before you start

- A **self-hosted Firecrawl instance** you run, reachable from the Choregos API. Choregos never uses
  Firecrawl's cloud (`*.firecrawl.dev`): it is refused when you save the connector and again when the
  platform builds it. Choregos does not deploy the instance for you.
- Choregos never asks Firecrawl to scrape: reading a result goes through `web_fetch`.
- The built-in `web_search` returns links and snippets, never pages.

## At the vendor

Nothing to create at Firecrawl. On your instance, note its address (for example
`http://firecrawl.internal:3002`) and, if you turned authentication on, the key it expects.

## Permissions

The key, if any, only searches. It stays in the API: no runner ever sees it. The API reaches the
instance's host and port, and only that: add it to the organisation's egress allowlist and keep it
out of the network ceiling's deny list.

## In Choregos

1. Connectors, **Web search**, type **Firecrawl**. Set `base_url` to your instance. If it has a key,
   type it in `api_key` (sealed, shown once).
2. Choregos does **not** know what your instance keeps of a query, nor what the engines behind it do:
   a self-hosted Firecrawl still sends the query to the search engines it uses (SearXNG, or whatever
   its upstream is), so "local" does not mean the query never leaves. Under a ceiling that caps
   retention or forbids reuse, the tool stays off until you fill `data_handling` from your own terms,
   for example `{"query_retention_days": 0, "query_reuse": false, "terms_ref": "runbook, section"}`. A
   declaration can only be stricter, and `terms_ref` is required.
3. The organisation's ceiling opens the tool: Settings, **Policy ceiling**, `web_search: {mode: allow}`.

## Test it

Press **Test**, then start a run: the agent's tool list shows `web_search`. Each query writes a ledger
row `search:firecrawl` and an event with the query's fingerprint, never its text.

## When it fails

- The save is refused with "base_url is required" or "Firecrawl's cloud": point `base_url` at your own
  instance. A connector saved before this decision has no `base_url`: its searches fail with the same
  message until you edit it.
- The tool is not offered: the ceiling is closed, the backend is not in its `backends`, or the declared
  data handling does not meet its bounds (an unknown value fails every bound that reads it).
- `query_redacted`: the query held what looks like a secret; nothing was sent.
- `rate_limited`: `requests_per_minute`, or your instance's own limit, is reached; the answer says when
  to retry.
- `network_policy`: the organisation's network ceiling refuses your instance's host.
