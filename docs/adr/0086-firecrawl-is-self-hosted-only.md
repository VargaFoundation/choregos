# 0086 — Firecrawl is self-hosted only, never Firecrawl's cloud

- **Status**: accepted, 2026-10-23 — implemented by S40-01k; amends
  [ADR 0083](0083-web-search-is-a-governed-tool-whose-backend-declares-what-it-keeps.md) §2
- **Concerns**: the `firecrawl` backend and connector type (`choregos_adapters.web_search.firecrawl`,
  the `("web_search", "firecrawl")` spec), the connector write routes of the API, the
  `connector.web_search.firecrawl` procedure, the `firecrawl` MCP catalogue entry; builds on
  [ADR 0066](0066-the-organisation-egress-allowlist.md) and
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (the organisation's network bound)

## Context

ADR 0083 §2 shipped `firecrawl` as a search backend "on Firecrawl's cloud or self-hosted", with the
cloud as the default (`https://api.firecrawl.dev`) and the key required only there. On 2026-10-23 the
project owner decided: **"Firecrawl must be local, not SaaS."**

The reason is the same one that makes ADR 0083 declare what a backend keeps: the query is the
sensitive part. On Firecrawl's cloud the query goes to a third party whose terms were never reviewed
(every field of its `data_handling` is unknown). On an instance the organisation runs, the first hop
is the organisation's own.

## Decision

1. **The `firecrawl` backend talks to a self-hosted instance only.** `base_url` is required and has no
   default. A `base_url` whose host is `firecrawl.dev` or a subdomain of it is refused.
2. **The refusal is enforced twice.** At configuration (a project or organisation connector write is a
   422 naming this decision) and in the adapter when it is built (defence in depth: a row written
   behind the API still cannot reach the cloud).
3. **A connector saved before this has no `base_url` and fails closed.** Its search is refused with an
   explicit error naming the decision; nothing is sent, and nothing falls back to the cloud. The
   organisation edits the connector and sets the address of its instance.
4. **The connector's egress is the configured `base_url`'s host and port**, and nothing else, so the
   organisation's network ceiling (ADR 0070) and its egress allowlist (ADR 0066) are checked against
   the host the platform really calls. With no `base_url` it declares no host.
5. **`api_key` stays optional**: a self-hosted instance may run without one. **No `scrapeOptions` is
   ever sent** (ADR 0083, "Alternatives rejected"), unchanged.
6. **The data handling stays undeclared.** "Local" does not mean the query never leaves: a self-hosted
   Firecrawl sends the query to the engines it searches with (SearXNG, or whatever its upstream is).
   Those terms are the organisation's to state, in `data_handling` with a `terms_ref`.

## Consequences

- A deployment that wants `firecrawl` runs its own instance. Deploying one is not part of this
  decision (infrastructure, other repositories).
- The `firecrawl` MCP catalogue entry (npm `firecrawl-mcp`) reaches the cloud unless it is pointed at
  an instance (`FIRECRAWL_API_URL`). The catalogue's entry schema cannot declare a required setting,
  and changing it is a catalogue-schema story of its own: finding #953, not decided here.
- Every ADR 0083 mention of Firecrawl's cloud is superseded by this record.

## Alternatives rejected

- **Keep the cloud as an opt-in.** The decision is that Firecrawl is not SaaS; an opt-in is the cloud
  one typo away.
- **A deny-list in the organisation's network ceiling only.** It protects the organisations that
  wrote it; the platform's own default must not be the cloud.
