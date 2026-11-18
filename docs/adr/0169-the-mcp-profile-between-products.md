# 0169 — The MCP profile between products: what Choregos exposes, what it consumes

- **Status**: accepted, 2026-11-15 (the product owner's decision D14 of the graal and Choregos offer of
  2026-11: MCP is one of the two doors between the products)
- **Concerns**: the MCP door (`apps/api/src/tranibor_api/mcp/`), the MCP connector and its client
  (`packages/adapters/src/tranibor_adapters/mcp/`, `specs.py`), the connector discovery
  (`routers/org_connectors.py`), the broker; follows [ADR 0030](0030-an-mcp-door-for-external-clients.md),
  [ADR 0079](0079-choregos-and-graal-are-autonomous-products.md),
  [ADR 0105](0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md); companion of
  [ADR 0168](0168-the-surfaces-frozen-for-1-0.md), which says what "frozen" means

## Context

Two products meet through two standard doors (ADR 0079, confirmed by D14): the agent platform **calls** the
data platform over MCP, and the data platform **calls** the agent platform over A2A (ADR 0078, 0131). A third
party, or an assistant of a person, uses the same two doors. So each side needs two things written down:
what its MCP server announces, and what its MCP client expects of a server it consumes. The offer calls that
a *profile*: the names, annotations and schemas of the tools two products agree on, versioned, and tested on
each side.

Facts as of `main` (0.27.6):

- **Exposed.** The door announces eleven tools on a project (`docs_search`, `docs_read`, `list_projects`,
  `describe_workflow`, `create_work_item`, `search_work_items`, `get_work_item`, `summarize_run`,
  `list_pending_decisions`, `get_pending_decision`, `draft_decision`) and, on `/mcp/assistants/{assistant}`,
  `ask`, `get_conversation` and prompts. Each announcement carries `annotations`:
  `readOnlyHint = not write`, `destructiveHint = false`, `idempotentHint = not write`,
  `openWorldHint = false`. Writes need the scope `mcp:write` and a right in the project. The door takes no
  decision for a person (ADR 0030 point 5): `draft_decision` deposits a draft the person takes in the
  console (ADR 0163).
- **Consumed.** A connector of type `mcp` talks to a server by `bearer`, `basic` or `oauth_client_credentials`
  (a token fetched from a `token_url` with a `client_id` and a sealed secret, RFC 6749 §4.4, S70-01); the
  token endpoint's host is added to what the organisation's egress ceiling must open (ADR 0066). A connector
  of type `mcp-gateway` consumes a server an organisation's gateway names (ADR 0105). On discovery, each
  tool becomes an *operation* with a policy (`allowed`, `approval`, `forbidden`; a new one starts
  `forbidden`) and a digest of its **input schema**; if the digest, or the read/write kind, differs on a
  later discovery, the operation goes back to `forbidden` until someone opens it again.
- **Not there.** No file lists the profile; no test compares `tools/list` with a list; the broker does not
  yet carry a `traceparent` to a connector nor record the `X-Request-Id` the server returns in `tool.called`
  (the offer's C2); the tool digest does not cover the tool's own `description`, `title` or `annotations`
  (only the read/write kind is compared), which is a gap for the reasons below.

## Decision

1. **A profile is a versioned file, not a convention.** `mcp-profile/v1`: for each tool its name, its
   annotations, the `operationId` of the REST operation it stands for (a policy is stated on an operation,
   and two tools can share one), and its input schema, **frozen to the byte, descriptions inside the schema
   included**, because the consumer's digest is computed on the whole schema. Each side holds a profile of
   the tools it exposes *and* a copy of the profile of the server it consumes, and tests against it.
2. **What Choregos exposes under the profile** is the table of the context, under these rules:
   - a tool's name, the names and types of its input fields and its read/write kind are frozen as ADR 0168
     decides; adding an optional field or a tool is free; anything else creates `<name>_v2` beside the old
     tool, which is served for at least 90 days;
   - the annotations are part of the profile, and must stay true: a tool announced `readOnlyHint: true`
     writes nothing, and a write is announced `mcp:write` and nothing else. A test that calls every read tool
     under a read-only token and finds no write belongs to the guard;
   - `destructiveHint` stays `false` on every tool of this door: the door deletes nothing. A tool that did
     would be a break of the profile, not an edition of it;
   - no tool of this door takes a human decision. This is not a default to be relaxed: it is what lets the
     other product call us without being able to approve in our place (ADR 0030 point 5, ADR 0131 point 4).
3. **What Choregos consumes under the profile** is a floor stated by the consumer, not trusted from the
   server:
   - one identity per gesture and per project on the server's side (a reader, a re-runner restricted to a
     list of jobs, an applier used only after a human decision, an auditor); the connector holds one
     `oauth_client_credentials` client per identity, never a person's token and never `*`;
   - a connector's operations start `forbidden`; the plugin of the data platform (which lives outside this
     repository, ADR 0079) declares the floors: reads `allowed`, writes only from a job step or a governed
     action, destructive tools and the decision tool `forbidden`;
   - a schema that moves closes the operation (as today). The profile's fingerprint extends this to the
     tool's `description`, `title` and `annotations` **only in profile v2**, when a semantic fingerprint that
     ignores `description`, `title` and `examples` exists (the offer's C11): until then, a changed
     description of a frozen tool is detected only by the profile test of the consumer's CI, not at run time.
4. **Failures are closed and named.** An absent, slow or erroring server, another version, or an answer the
   broker did not expect (a 202 for a write that needed no approval) fails the step that called, with the
   reason, in a bounded time. Nothing falls back to another path. *This is a requirement this record states;
   the "degraded peer" suite that proves it is not written* (the offer's IND-3).
5. **Both directions are tested against a third party**, not only against the other product: the consumer
   against a fake MCP server whose tools are renamed, then a real third-party server; the exposed door
   against a reference MCP client. The test that names both products lives in the repository that integrates
   them, never here (ADR 0079 point 5).
6. **Correlation.** The broker will carry the caller's `traceparent` to the connector and record the
   `X-Request-Id` the server returns in the `tool.called` event, so that an auditor can join a ticket to the
   server's audit by that identifier (the offer's C2, E5). The server opens its own trace and *links* to ours;
   it never adopts our identifier as its audit key.
7. **Where each part lives.** The profile of the door will live in `packages/contracts` (a contract change,
   ADR 0001), generated from the tool definitions so that it cannot drift from them. The consumer's copy and
   its fake live with the integration (`packages/adapters` tests), pinned by digest.

## Consequences

- The data platform can depend on the names and schemas of our eleven tools, and we on its tools, and each
  side can say what broke.
- Decision 1 needs a file and a test that do not exist: STATUS carries "profile, exposed side" and "profile,
  consumed side" as `⬜`. Until they land, nothing in this record is *guarded*; the facts of the context are
  all that is.
- `oauth_client_credentials` exists (S70-01); that a real server of the other product accepts it, with a
  token of a few minutes and a realm-derived tenant, is **not proven here**: it is proven on the integration
  side, or not at all.
- The profile adds friction to every MCP tool change on the door: a tool is now an interface with a date.

## What this record does not decide

- The list of tools the data platform exposes, and its identities: they are its own, and it is the one that
  writes them.
- Running a profile test on the data platform's pull requests: a decision of its repository, not of ours.
- Delegation of a person's identity over MCP (RFC 8693, `user_consent`): a later record, if a customer asks.

## Alternatives discarded

- **No profile: each side reads the other's `tools/list` at run time.** Is what the connector does today; it
  detects a change after it happened, in production, as a closed operation.
- **A profile of descriptions and names only.** The consumer's digest is computed on the schema; a profile
  that does not freeze it exactly gives the false comfort of a green test and a closed operation.
- **One profile for both directions.** The two sides expose different tools; a shared file would couple the
  release of one product to the other, which is what ADR 0079 forbids.
