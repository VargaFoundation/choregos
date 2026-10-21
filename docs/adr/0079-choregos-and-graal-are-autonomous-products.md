# 0079 — Choregos and graal are autonomous products: they meet only through public interfaces

- **Status**: accepted, 2026-10-20
- **Concerns**: the bridge to a data platform (stream S14), the catalogue (ADR 0014, ADR 0074), the
  MCP door (ADR 0030), identity (ADR 0029 §3), the editions (ADR 0024); amends
  [ADR 0028](0028-the-governed-execution-of-change.md) §4 ("share identity (one OIDC realm)"), whose
  S14 stream ADR 0029 kept

## Context

graal, the sovereign data and AI platform built next to Choregos, decided on 2026-10-19 (its own ADR
0082, in its repository) that the two are two open-source products, "independent but complementary":
each installs, is used and is bought on its own. Choregos had only half of that in writing. ADR 0028
§4 says "no code merge", then "two products share identity (one OIDC realm)", which reads as a
requirement. Nothing in this repository refused a dependency either: a `GRAAL_API_URL` in the
configuration or a graal client in `packages/adapters` would have turned no test red.

The question came back with features that need both products: a report whose figures stay live,
rendered with the viewer's rights (graal's data, Choregos's governed production), and agents that
drive graal's jobs. Each of them can be built as a dependency or as two generic capabilities. The
product owner chose on 2026-10-20: the products stay autonomous.

## Decision

1. **Each product installs, runs, upgrades and is bought alone.** Every Choregos feature works with
   no graal deployed. A data platform, graal or another, is a catalogue entry among others, not a
   component of Choregos.
2. **They meet only through public interfaces**: graal's MCP server reached as a catalogue connector
   through the broker (ADR 0014, ADR 0074), Choregos's MCP door for clients on graal's side
   (ADR 0030), A2A (ADR 0062), webhooks, and the customer's identity provider. Never a shared
   database, a shared library, an imported contract, a chart dependency or a host name written in
   the code.
3. **Identity is the customer's, configured on each side.** Each product is set up against the
   customer's OIDC provider on its own. Both may use the same realm; that is a deployment choice,
   not a requirement (this amends ADR 0028 §4).
4. **A feature that needs both is a generic capability on each side.** A report's live references
   resolve through any MCP connector (graal, Insights360 or another), with the viewer's token where
   the connector declares it (ADR 0077, proposed in #758). A report format that graal renders is a graal feature that
   Choregos produces as any other MCP client would. The data-change template (S14-03) runs against
   any data platform's MCP server, and against a fake in CI.
5. **The boundary is a test.** `tests/licences/test_independent_products.py` refuses any mention of
   graal in the code, charts, templates, demo and deployment files. It allows three things, each for
   a reason: the MCP catalogue file (an entry is optional data, which S14-01 will add), the enterprise
   edition's branding (GraalSystems publishes it under ADR 0024; a name in a footer is not a runtime
   dependency), and the `graalpy` wheel tags of third-party packages.
6. **The reverse guard is graal's.** That graal works without Choregos is for graal's repository to
   test; this repository can only prove its own half.

## Consequences

- S14-01 stays a catalogue entry, S14-03 a template with a fake. Neither may add code that names
  graal.
- A customer can buy Choregos to govern agents on Databricks, Snowflake or their own warehouse, and
  graal to run data without agents: the offer that bundles both is commercial, not technical.
- Features that would be quicker with a direct link (graal's database read by Choregos for lineage,
  a shared session cookie) are built the longer way, through MCP and OIDC. That cost is accepted.
- The edition's product name ("Graal Agentic Platform") stays branding. If a release ever needs
  graal at runtime under that name, it is a breach of this decision, not an edition feature.

## Rejected alternatives

- **A graal client in `packages/adapters`.** Faster for S14, and it would make Choregos the place
  where graal's API changes break things. MCP is the contract graal already publishes.
- **An umbrella chart that installs both.** It turns the bundle into a dependency at the first
  shared value. Each chart stays installable alone; a deployment repository may install both.
- **One OIDC realm, required.** Simpler to document, and it would forbid the customer who runs
  Choregos against Entra and graal against its own Keycloak.
