# Architecture decision records

A structural decision is written here before it is coded, and re-read whenever someone
asks "why is it like this?". Format: context, decision, consequences, alternatives
discarded. A reversed decision is not erased: it is marked *superseded by*.

English is the reference language since 2026-09-24. The French originals of the records
written before that date are kept in [`docs/fr/adr/`](../fr/adr/) and are no longer
maintained; the files below keep their original names, which are their identifiers.

| # | Decision (English summary) | Status |
| --: | :-- | :-- |
| 0001 | Contracts are frozen at M0 and versioned — `packages/contracts` is the single source of truth | accepted |
| 0002 | ACP (Agent Client Protocol) as the agent contract; the OpenHands default was superseded by 0011 | accepted |
| 0003 | Temporal, self-hosted, for durable orchestration — one workflow per ticket | accepted |
| 0004 | Cost is counted at the LiteLLM gateway, never from the agent's claim; one capped virtual key per run | accepted |
| 0005 | Every connector is a `Protocol` with a real and a scriptable fake implementation | accepted |
| 0006 | Three independent production locks: merge queue, release train, declarative guardrails | accepted |
| 0007 | Memory must earn its place before anything depends on it | accepted |
| 0008 | Deterministic identifiers and idempotence everywhere | accepted |
| 0009 | The cluster changes only through Git (GitOps as the only mutation path) | accepted |
| 0010 | A guarantee is a mechanism, never a prompt — a gate that cannot see refuses | accepted |
| 0011 | OpenHands removed: it exposes no CLI ACP agent; `claude-code` becomes the default | accepted |
| 0012 | The engine is not tied to software: deployment playbooks, generic gates, internal tracker, optional repo, business evidence, open roles | accepted |
| 0013 | Agent task density: an admission queue (suspended Jobs), executor capabilities — and what is taken from google/ax | accepted |
| 0014 | A platform-held tool catalogue: HTTP and external MCP tools called *for* the agent, keys server-side, per-group access | accepted |

## Index

| # | Decision | Status |
| --: | :-- | :-- |
| [0001](0001-contrats-geles.md) | Contracts are frozen at M0 and versioned | accepted |
| [0002](0002-acp-comme-contrat-agent.md) | ACP as the agent contract; OpenHands default superseded by 0011 | accepted |
| [0003](0003-temporal-comme-orchestrateur.md) | Temporal for durable orchestration | accepted |
| [0004](0004-cout-compte-au-gateway.md) | Cost is counted at the gateway, not by the agent | accepted |
| [0005](0005-adaptateurs-et-fakes.md) | Every connector has an interface and a fake | accepted |
| [0006](0006-trois-verrous-de-production.md) | Three independent locks protect production | accepted |
| [0007](0007-memoire-optionnelle.md) | Memory must earn its place before anything depends on it | accepted |
| [0008](0008-idempotence-et-run-id.md) | Deterministic identifiers and idempotence everywhere | accepted |
| [0009](0009-gitops-seule-source-de-verite.md) | The cluster changes only through Git | accepted |
| [0010](0010-les-gates-sont-des-mecanismes.md) | A guarantee is a mechanism, never a prompt | accepted |
| [0011](0011-retrait-d-openhands.md) | OpenHands removed; `claude-code` becomes the default | accepted |
| [0012](0012-le-moteur-n-est-pas-lie-au-logiciel.md) | The engine is not tied to software | accepted |
| [0013](0013-densite-des-taches-d-agent.md) | Agent task density, and what we take from AX | accepted |
| [0014](0014-un-catalogue-d-outils-tenu-par-la-plateforme.md) | A platform-held tool catalogue | accepted |
