# 0024 — Two editions: a community core, an enterprise layer

- **Status**: accepted, 2026-09-26 — reopens decision D1 of `docs/plan/00-index.md`
- **Concerns**: the licence of the repository, what the API refuses, how the enterprise layer is built and shipped

## Context

Choregos is six days old, public, Apache-2.0, with no stars and no forks. It answers an
enterprise question — *what did the agent do, under which contract, who signed off, what did it
cost, and can I prove it?* — and almost everything that makes it valuable is already published
under a permissive licence.

Two facts set the shape of this decision.

**The market does not pay for what we already give away.** Every enterprise tier in this
category charges for the same list: SAML SSO and SCIM, an audit trail, RBAC, self-hosted or VPC
deployment, budget controls, central billing, support. Choregos ships self-hosting, RBAC and
budgets in the open core. The paid axis therefore cannot be the "SSO tax"; it has to be
governance at scale, compliance evidence, chargeback and multi-tenant operations.

**The multi-tenancy we advertise is half-built.** Twelve tables are under row-level security;
thirteen were not, and `audit_log` had no organisation column at all until 2026-09-26 — any
`project_owner` could read every organisation's audit trail. A single-organisation install was
never exposed to that class of bug, because there is nothing to cross.

And removing anything from a public Apache-2.0 project is only cheap while nobody depends on
it. That window is open now and will not be open in three months.

## Decision

**The community edition is single-organisation. Multi-organisation, and the enterprise identity
surface that only matters with it, belong to a separate enterprise edition.**

1. **Licence.** The core stays **Apache-2.0**, published by the Varga Foundation. The enterprise
   edition is proprietary, lives in a **separate private repository**, and is published by
   Diametral. Apache-2.0 allows this without reservation: it has no copyleft.
2. **What the community edition keeps.** Everything that works today except a second
   organisation: the DSL and its static validation, the Temporal interpreter, the fourteen
   gates, evidence, the cost ledger with its per-run cap, the tool catalogue, ACP and its
   backends, release trains, all twenty connectors and their fakes, RBAC, **OIDC with discovery
   and PKCE**, **fail-closed RLS**, API and run tokens, runner guardrails, the egress proxy,
   metrics, dashboards, SSE, the CLI, all fourteen screens, and the Helm chart.
3. **What leaves.** The ability to create a **second organisation**; per-organisation OIDC group
   mapping; and the organisation-scoped meaning of `/platform/*` and `/audit`. Nothing that is
   *proven* today is lost: a single-organisation install behaves exactly as before.
4. **RLS stays in the community edition.** It protects the one organisation, it is inseparable
   from the data layer, and shipping a knowingly weaker security posture to the community would
   be indefensible.
5. **No licence key in the first enterprise version.** The enterprise edition is run by
   Diametral, for Diametral and its tenants; access control is the image pull secret. A signed
   offline key is a question for the day a customer self-hosts it.
6. **The chart stays in the community edition.** Tenants vendor the published chart, which
   overwrites everything but their values file, so any change the enterprise edition needs is
   released upstream first.

## Consequences

- The community edition stops advertising a multi-tenancy it had not finished. That is a gain in
  honesty before it is a loss of feature.
- The enterprise edition must *finish* the isolation — the thirteen tables, the organisation
  lifecycle, per-organisation ceilings, noisy-neighbour control — and prove it on PostgreSQL.
  That is the work being paid for.
- **The core must grow real extension seams before any of this is possible.** There are none
  today: `grep entry_points` over `packages/` and `apps/` returns nothing outside tests, and the
  adapter registry is a table filled in by hand. Playbooks are the only capability extensible
  from outside the tree, and they are the model to copy.
- Two repositories mean two pipelines and a compatibility matrix to hold.
- A trademark and a copyright holder become load-bearing: `LICENSE` has an empty appendix, there
  is no `NOTICE`, and no CLA or DCO. Recorded here so it is not discovered later.
- This record **reopens D1** of `docs/plan/00-index.md` ("Apache 2.0", listed as not to be
  reopened without a `decision` pull request). This is that pull request.

## Alternatives discarded

- **An `ee/` directory in this monorepo under BSL 1.1.** One repository, one CI, no branches to
  keep in step — the GitLab model. Discarded by the owner in favour of a clean legal boundary.
- **Nothing closed; sell only the service.** Zero friction for contributors, revenue entirely
  tied to running the platform. Discarded: it makes every customer an operations customer.
- **Dual licence AGPL plus commercial.** Protects against a third party reselling it as a
  service, but the AGPL is a frequent veto in large accounts and would deter contributors.
- **Gating simple SSO.** Competitors put it in their team tier, and a community edition whose
  only login is the development back door would have no security at all. OIDC with discovery and
  PKCE stays open; SAML, SCIM and deprovisioning are what the enterprise edition sells.
- **A free ceiling of three organisations.** A number rather than a capability, worked around by
  a one-line fork, and it protects nothing an enterprise actually buys.
