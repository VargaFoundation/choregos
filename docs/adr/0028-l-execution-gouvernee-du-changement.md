# 0028 — Choregos governs the execution of change, not agents in general; data work enters through the catalogue and the gates

- **Status**: superseded by [ADR 0029](0029-une-plateforme-d-agents-gouvernes.md) on 2026-10-05 (accepted 2026-10-02)
- **Concerns**: what Choregos builds and what it plugs into, the catalogue (ADR 0014), gates and
  evidence (ADR 0010, ADR 0012), the authorship attestation (ADR 0015), the bridge to a data
  platform (graal first)

## Context

Two questions came up once the enterprise layer reached E4. Should Choregos grow into a horizontal
"agent control plane", one that governs every agent of a company, whatever it does? And should it
merge with graal, the sovereign data and AI platform built next to it, to offer one agentic platform
for code and data?

The market answered the first question in 2025–2026. Horizontal agent governance is being absorbed
by companies that own identity, networks or data:

- **Identity**:
  [Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/whats-new-agent-id),
  and [Okta Agent SSO](https://www.okta.com/newsroom/press-releases/okta-brings-first-class-identity-to-ai-agents-with-agent-sso/)
  included in core plans at no extra cost.
- **MCP gateways**: Kong, Cloudflare and IBM ContextForge. Natoma was
  [bought by Snowflake](https://www.theregister.com/ai-and-ml/2026/05/28/snowflake-buys-natoma-to-help-freeze-out-rogue-agents/5248062),
  and Portkey by Palo Alto Networks.
- **Observability**: Langfuse was bought by
  [ClickHouse](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability),
  and Arize by [Dynatrace](https://www.dynatrace.com/news/press-release/dynatrace-to-acquire-arize/).
- **Data platforms**: Databricks' Unity AI Gateway
  [already governs coding agents](https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway),
  with spend caps and approvals before a push.
- **Analysts**: Forrester profiles 33 vendors in the "agentic control plane" landscape.

**The narrower slice is open.** Agentic delivery that runs **entirely** in the customer's perimeter
is rare:
[GitLab Duo Self-Hosted](https://about.gitlab.com/blog/more-ai-models-for-duo-agent-platform-self-hosted/),
[Factory Private](https://factory.com/news/factory-private) (private preview), Tabnine, OpenHands, Qodo,
[Mistral Code](https://mistral.ai/news/mistral-code/). The answer of the US leaders is a split plane: their cloud keeps the agent loop, the customer
keeps the execution. Nobody ships a **signed** attestation of AI authorship either.
[Agent Trace](https://agent-trace.dev/) is unsigned, and the
[in-toto predicate proposal](https://github.com/in-toto/attestation/issues/604) opened on 2026-09-30
has no answer yet.

**The second question has an answer in the code.** graal is Java (Spring Boot), Choregos is Python
(Temporal). graal orchestrates its runs in-process, without CRDs, and is proprietary; Choregos's
core is Apache-2.0. What they share is a contract: graal exposes MCP tools, and Choregos already
knows how to call third-party tools for an agent without giving it a credential (ADR 0014).

## Decision

1. **Choregos's scope is the governed execution of change.** Agent work that ends as a versioned
   change goes through the same path:
   - ticket → pull request → gates that refuse → human approval with re-authentication → GitOps
     → signed attestation;
   - it runs in the customer's perimeter, whatever the agent;
   - "change" covers code, infrastructure as code, manifests, dbt models, SQL migrations, DAGs and
     job definitions on a data platform.
2. **Choregos does not build an identity provider, an MCP gateway, a sandbox runtime or an
   observability product.** It plugs into them: `agent-sandbox` (ADR 0016), OIDC and SPIFFE (ADR
   0021), OpenTelemetry GenAI (ADR 0018), in-toto, CycloneDX.
3. **Data work enters through two seams that already exist, not through a merge:**
   - **the catalogue** (ADR 0014): a data platform's write tools (graal's `create_job`, `run_job`,
     `schedule_job` first) become catalogue entries called by the platform for the agent, which
     holds only its run token;
   - **the gates**: a data change is judged by mechanisms — tests, data quality measured on a
     sample, `evidence_facts` read from the platform's run (ADR 0012) — and applied through the
     platform's API after approval.

   Exploratory, read-only use of a data platform's MCP stays in that platform, under its own bounded
   agent identity.
4. **No code merge with graal.** Two products share identity (one OIDC realm), audit conventions and
   the namespaced install model. They are sold as one offer when a customer buys both.
5. **The first user is a systems integrator delivering with it** (Diametral); the second segment
   is regulated group IT. Both need per-client isolation, chargeback and a client-facing evidence
   pack — the enterprise edition's E4 and E5.

## Consequences

- A new backlog stream, **S14 — governed change on a data platform**: catalogue entries for a data
  platform, a `data_quality` gate, a `data-change` template, and an adoption report. Each story is
  one pull request with a test that fails without it.
- The enterprise edition's next steps follow from point 5: the signed attestation, the evidence pack
  and chargeback per client. They are tracked in the enterprise repository, not here.
- `docs/positioning.md` already says "Choregos is the delivery layer… not a coding agent, and not a
  sandbox runtime"; this record adds "and not a horizontal agent control plane". Nothing there
  contradicts it.
- What we give up:
  - an agent registry, a gateway, cross-vendor agent discovery;
  - ad-hoc question-answering agents over data, left to the data platforms that already ship them.
- The bridge to graal is useful only once graal's write tools have a real effect. Until then, the
  S14 stories are built and tested against fakes (ADR 0005) and against any other platform that
  exposes the same tools.

## What would change our mind

- **The orchestrator scope is wrong.** If the integrator's own squads do not route real client work
  through Choregos in their first deployment, we narrow Choregos to an evidence and attestation
  layer on top of the agents of GitHub and GitLab.
- **The attestation stops being a differentiator.** If a signed AI-authorship predicate becomes a
  standard and the large platforms ship it, we follow the standard and compete on gates and
  evidence.
- **A competitor ships the same perimeter-complete path first.** If Factory Private, GitLab Duo
  Self-Hosted or Mistral AI Studio — whose agent runtime is also built on Temporal — ship it with
  equivalent gates, Choregos's value moves to what they lack: agent-agnostic orchestration,
  multi-tenant delivery for integrators, and non-code work.

## Alternatives discarded

- **A horizontal agent control plane** (registry, gateway, identity, cross-vendor monitoring). It is
  a capital fight, already absorbed by identity, security and data vendors.
- **One codebase for graal and Choregos.** Java against Python, in-process orchestration against
  Temporal, proprietary against Apache-2.0, and two front ends. A contract gives everything a merge
  would give, sooner.
- **A data runtime inside Choregos.** It would duplicate the platforms the customers already run.
