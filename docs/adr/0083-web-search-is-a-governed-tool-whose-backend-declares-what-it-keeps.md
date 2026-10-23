# 0083 — Web search is a governed tool whose backend declares what it keeps

- **Status**: accepted, 2026-10-20; amended 2026-10-23 by [ADR 0086](0086-firecrawl-is-self-hosted-only.md)
  (§2, `firecrawl` is self-hosted only) — implemented by S40-01g except the `vertex-enterprise` adapter
  (deferred, §2) and the region bound on the shipped adapters, whose terms were not reviewed (§3)
- **Concerns**: the contract (`policy.schema.json`, `PolicyCeiling`, `openapi.yaml`), the adapters (a
  `SearchBackend` port, its fake and its first adapters), the API (`routers/internal.py`, a
  `services/web_search.py` beside `services/web_fetch.py`), the presets; builds on
  [ADR 0005](0005-adapters-and-fakes.md) (fakes), [ADR 0015](0015-ai-authorship-attestation.md) (a
  prompt is fingerprinted, not disclosed), [ADR 0034](0034-connectors-by-capability.md) (connectors by
  capability, a discovered tool is born closed), [ADR 0060](0060-a-gateway-agnostic-model-catalogue.md)
  (gateway capabilities), [ADR 0066](0066-the-organisation-egress-allowlist.md) (the organisation's
  egress allowlist) and [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (network access
  per run, `web_fetch`)

## Context

An agent can *read* a page through the governed `web_fetch` (ADR 0070, S40-01f). It cannot *find* one
through the platform. Today it has three ways to search, and none of them is governed on the point that
matters most for search:

- **The agent CLI's own `WebSearch`.** It runs at the model provider, outside every Choregos network
  layer. ADR 0070 rates it **advisory**: the platform can only switch it off (`builtin_web_tools: deny`,
  set by the `team` and `regulated` presets; `solo` keeps the default `allow`).
- **Provider-native grounding** (Gemini `googleSearch` or `enterpriseWebSearch`, Anthropic's
  `web_search` server tool, OpenAI's `web_search_options`). It is the same thing seen from the gateway:
  nothing in Choregos configures it, refuses it, or records a query.
- **A search MCP server from the catalogue** (`tavily`, `brave-search`, `exa`, `perplexity`, `kagi`,
  `firecrawl`). It is a connector (ADR 0034, 0074): the policy, the ledger and the injection guard apply.
  But nothing says what the provider does with the queries.

A search query is outbound text written by an agent that has read the work item, the repository and
the memories. "What happens to the queries, the prompts and the results?" is the question an enterprise
asks before it enables web search at all. A reference answer is to choose the provider for its data
handling, for example:

| Aspect | Google Search grounding (standard) | Vertex AI Web Grounding for Enterprise |
| :-- | :-- | :-- |
| Index | the whole google.com index | a filtered subset of the public index |
| Query retention | kept 30 days | not kept |
| Query reuse | used to improve the index | not used |

Choregos has no notion of what a provider keeps. Today a regulated organisation cannot say "search,
but only through a provider that keeps nothing". It can only forbid every web tool.

## Decision

**1. `web_search` is a built-in tool served by the API, like `web_fetch`.**
- It is offered to a run when three conditions hold:
  - the effective policy allows it;
  - the organisation has a search backend;
  - that backend's declared data handling meets the organisation's ceiling.

  Otherwise it answers 404, like `web_fetch`.
- Its name is reserved: another tool cannot take it (409, as in `routers/internal.py` for `web_fetch`).
- Input: `query`, an optional `max_results` (capped), and optional `site` and `recency` filters when
  the backend supports them.
- Output: a list of `{title, url, snippet, published_at?}`. Each result also carries `fetchable`,
  which says whether the run's effective network would let `web_fetch` read it.
- **Search returns links and snippets, never pages.** Reading a result goes through `web_fetch`, which
  holds the allowlist, robots.txt and politeness.

**2. A search backend is an adapter behind a `SearchBackend` port.**
- A scriptable `FakeSearchBackend` is mandatory and selected by `CHOREGOS_FAKES=1` (ADR 0005).
- The organisation configures its backend as a connector of capability `web_search` (ADR 0034). The key
  is sealed in the API and never reaches a runner. The backend's host goes through the organisation's
  egress allowlist for the `api` family (ADR 0066).
- First adapters, in the order a deployment is likely to need them:
  - `vertex-enterprise`: Vertex AI Web Grounding for Enterprise. It is a grounded Gemini call, so it goes
    **through the gateway** as an auxiliary call. This needs a gateway capability (ADR 0060) saying the
    gateway passes the `enterpriseWebSearch` tool through; the adapter refuses to start without it.
  - `firecrawl`: its search endpoint, **on a self-hosted instance only** (amended 2026-10-23 by
    [ADR 0086](0086-firecrawl-is-self-hosted-only.md); it was "on Firecrawl's cloud or self-hosted").
  - `brave` and `tavily`: plain search APIs.

**3. Every backend declares its data handling, and the declaration is code.**

```yaml
data_handling:
  query_retention_days: 0       # null: unknown
  query_reuse: false            # used to train, rank or re-index; null: unknown
  region: eu                    # where queries are processed; null: unknown
  terms_ref: https://…  (dated) # the document the declaration comes from
```

- The adapter carries a reviewed default.
- An organisation may only make the declaration **stricter**, for example when an enterprise contract
  grants zero retention. It must then give a `terms_ref`, and the change is audited (`policy.ceiling`).
- An unknown value (`null`) fails every bound that reads it.

**4. The ceiling bounds it, the project narrows it.**
- `PolicyCeiling.web_search` holds:
  - `mode: allow | deny`, default `deny`: a capability is born closed;
  - `backends`: the allowed adapter types;
  - `max_query_retention_days`: `null` means no cap;
  - `allow_query_reuse`: default `false`;
  - `regions`.
- `Policy.web_search.mode` lets a project only narrow it; `widenings()` and `clamp()` treat it like the
  network section.
- The `regulated` preset sets these bounds:
  - `max_query_retention_days: 0`;
  - `allow_query_reuse: false`;
  - `builtin_web_tools: deny` (already set).
- There is no `approval` mode. A search is a read (ADR 0034: approval is for writes), and its cost is
  bounded by `tool_calls_per_run`.

**5. The outbound query is checked, not rewritten.**
- The query goes through `choregos_core.redaction`.
- If redaction changes it (a known secret or a token pattern), the call is refused with 422
  `query_redacted`. The agent is told why; the query is not silently altered, and nothing is sent.
- The same check is due for `web_fetch` URLs, which sent their path and query unchecked. That was
  recorded here as a finding, not fixed by this decision.
- **As delivered (S40-01h, finding #785):** `web_fetch` applies the rule to the address it is given
  (as written and percent-decoded; the fragment never leaves the client). If redaction would change it,
  the call is refused with 422 `url_redacted` before any DNS lookup or robots.txt request, journaled in
  `network_decisions` (`denied`, reason `redacted`), and not counted in the ledger. `web_fetch` sends
  no request body, so the address is the only outbound text. Redirect targets are chosen by the
  remote site, not the agent, and are not checked.

**6. Audit without disclosure.** Each call:
- writes a ledger row (`kind = tool`, provider `search:<backend>`, the backend's declared price);
- counts in `tool_calls_per_run`;
- emits `choregos.tool.called` with the query's **fingerprint** and length, not its text (ADR 0015);
- passes its results through the injection guard, like a connector's answer.

A per-organisation rate per backend answers 429 with `retry_after_s`.

## Alternatives rejected

- **Enable provider-native grounding in the agent's own model calls.** It runs at the provider: no
  allowlist, no redaction, no row per query, and the data handling is whatever the agent's model
  provider applies to its server tools. It stays advisory and off where `builtin_web_tools` is `deny`.
  - Making the gateway **refuse** server-side search tools on agent calls would turn this into a wall.
  - That is gateway configuration in the deployment repositories, recorded as a follow-up.
- **Rely on the catalogue's search MCP servers.** They stay available as connectors, but no declaration
  says what their providers keep, so a ceiling cannot bound them. When their catalogue entries gain the
  same `data_handling` block, the ceiling can apply to them too. That is a follow-up, not this decision.
- **Return pages fetched by the search provider** (Firecrawl's search with scraping, Tavily's raw
  content).
  - The page is fetched from the provider's infrastructure, under its User-Agent, outside robots.txt as
    Choregos reads it and outside the run's allowlist. Only the provider's API host would be visible.
  - Deferred: reading stays with `web_fetch`.
  - Whether a third-party fetcher may render JavaScript pages for `web_fetch` is a separate decision.
- **A self-hosted meta-search engine (SearXNG) as the answer to retention.** The query still reaches the
  upstream engines it aggregates. It may become an adapter, and its declaration must then say so.
- **Store the query text in the event for audit.** The query can carry what the ticket says
  (ADR 0015 §"fingerprinted, not disclosed"). An organisation that wants the text can log it at its
  backend.

## Consequences

- S40-01g ships `brave`, `tavily` and `firecrawl` declaring **every field unknown**: their terms were
  not reviewed, so under any retention cap or the default refusal of reuse an organisation must state
  its contract (`data_handling` with `terms_ref`) before the tool is offered. The fake declares zero
  retention and no reuse.
- The `regulated` preset carries the bounds on the **policy** side (`web_search:
  {max_query_retention_days: 0, allow_query_reuse: false}`); the ceiling still has to open the tool.
- A project's `mode: allow` (the default) is not a widening under a ceiling's `deny`: it means "as far
  as the ceiling lets", so a project that never mentions web search still saves under a closed ceiling.
- `vertex-enterprise` is not shipped: it needs the gateway capability of §2, which no deployment
  declares yet.
- S40-01g is done when each guard has a test that turns red if the guard is removed:
  - not offered under `mode: deny`, under an unknown or too-long retention, or under a disallowed
    backend;
  - 409 on a name clash;
  - 422 `query_redacted` with nothing sent to the fake;
  - a ledger row and the event carry the fingerprint and never the text;
  - `fetchable` follows the run's effective network;
  - the injection guard runs on the results;
  - a project cannot widen the ceiling.
- The `vertex-enterprise` adapter needs the deployment's gateway to pass the grounding tool through.
  Until a live test proves that (`make test-live`), its adapter stays 🟡.
- Whether `solo` should switch built-in web tools off once `web_search` exists is left with the open
  question in the plan of 2026-10-16 (item 1, "left to the operator").
