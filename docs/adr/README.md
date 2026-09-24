# Architecture decision records

A structural decision is written here before it is coded, and re-read whenever someone
asks "why is it like this?". Format: context, decision, consequences, alternatives
discarded. A reversed decision is not erased: it is marked *superseded by*.

> The records below are in French (written before 2026-09-24, when English became the
> reference language). New records are written in English; the existing ones are
> translated as they are touched. Each row carries an English one-line summary.

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

## Index (French titles)

| # | Décision | État |
| --: | :-- | :-- |
| [0001](0001-contrats-geles.md) | Les contrats sont gelés à M0 et versionnés | acceptée |
| [0002](0002-acp-comme-contrat-agent.md) | ACP comme contrat d'agent, OpenHands par défaut | acceptée |
| [0003](0003-temporal-comme-orchestrateur.md) | Temporal pour l'orchestration durable | acceptée |
| [0004](0004-cout-compte-au-gateway.md) | Le coût se compte au gateway, pas chez l'agent | acceptée |
| [0005](0005-adaptateurs-et-fakes.md) | Tout connecteur a une interface et un fake | acceptée |
| [0006](0006-trois-verrous-de-production.md) | Trois verrous indépendants protègent la production | acceptée |
| [0007](0007-memoire-optionnelle.md) | La mémoire doit faire ses preuves avant qu'on en dépende | acceptée |
| [0008](0008-idempotence-et-run-id.md) | Identifiants déterministes et idempotence partout | acceptée |
| [0009](0009-gitops-seule-source-de-verite.md) | Le cluster ne se modifie que par Git | acceptée |
| [0010](0010-les-gates-sont-des-mecanismes.md) | Une garantie est un mécanisme, jamais un prompt | acceptée |
| [0011](0011-retrait-d-openhands.md) | Retrait d'OpenHands : aucun agent ACP en ligne de commande | acceptée |
| [0012](0012-le-moteur-n-est-pas-lie-au-logiciel.md) | Le moteur n'est pas lié au logiciel (playbooks, garantie générique, tracker interne) | acceptée |
| [0013](0013-densite-des-taches-d-agent.md) | La densité des tâches d'agent : file d'admission, capacités d'exécuteur | acceptée |
| [0014](0014-un-catalogue-d-outils-tenu-par-la-plateforme.md) | Un catalogue d'outils tenu par la plateforme (HTTP et MCP extérieur, par groupes) | acceptée |
