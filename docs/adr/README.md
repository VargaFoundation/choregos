# Architecture decision records

A structural decision is written here before it is coded, and re-read whenever someone
asks "why is it like this?". Format: context, decision, consequences, alternatives
discarded. A reversed decision is not erased: it is marked *superseded by*.

English is the reference language since 2026-09-24. The French originals of the records
written before that date are kept in [`docs/fr/adr/`](../fr/adr/) and are no longer
maintained; the files below keep their original names, which are their identifiers.

The table below is the whole list, and it is checked by `tests/docs/test_index_des_adr.py`:
every file in this folder appears in it, with the status the file itself declares. A second,
hand-kept summary table used to live here; it silently stopped at 0014 and told readers that
nine records did not exist.

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
| [0015](0015-attestation-de-paternite-ia.md) | A signed "AI authorship" attestation per merged change | proposed |
| [0016](0016-executeur-agent-sandbox.md) | An `agent-sandbox` executor: warm pools, suspend across a human gate, snapshots | proposed |
| [0017](0017-conformite-mcp-2026-07-28.md) | MCP 2026-07-28: stateless tools, tasks, elicitation, resource-bound OAuth | proposed |
| [0018](0018-otel-genai.md) | OpenTelemetry GenAI spans from the runner and the gateway; the ledger stays the truth for money | proposed |
| [0019](0019-temporal-workflow-streams.md) | Temporal Workflow Streams as the transport of a run's live journal | proposed |
| [0020](0020-evals-de-trajectoire.md) | Trajectory evals in the nightly matrix, scored from the journal by mechanisms | proposed |
| [0021](0021-spiffe-derriere-le-jeton-de-run.md) | SPIFFE identities behind the run token: revocation by deletion, mTLS to the gateway | proposed |
| [0022](0022-catalogue-federable.md) | A federable catalogue: registry entries become pull requests, born closed | proposed |
| [0023](0023-constructeur-visuel-de-workflow.md) | A visual builder: read-only map first, then edits that are YAML diffs | proposed |
| [0024](0024-deux-editions.md) | Two editions: a single-organisation community core, a separate enterprise layer | accepted |
| [0025](0025-le-moteur-pilote-l-it-de-la-plateforme.md) | The engine drives platform IT: healthcheck findings become governed changes, nodes last | accepted |
| [0026](0026-ce-que-la-rls-du-coeur-couvre.md) | What the core's RLS covers — every table checked; identity included since 0.11 (update of 2026-09-29) | accepted |
| [0027](0027-le-voisinage-se-regle-a-l-admission-et-au-quota.md) | Noisy neighbours: bounded at admission and by a per-organisation namespace quota, not by a Temporal queue per organisation | accepted |
| [0028](0028-l-execution-gouvernee-du-changement.md) | Choregos governs the execution of change, not agents in general; superseded by 0029 | superseded |
| [0029](0029-une-plateforme-d-agents-gouvernes.md) | Choregos is a general-purpose platform for governed agents: several workflows per project, an agent registry, skills, MCP, connectors by capability | accepted |
| [0030](0030-une-porte-mcp-pour-les-clients-externes.md) | A door for external MCP clients inside the API: `/mcp`, scoped tokens first, OAuth next, no decision through MCP | accepted |
| [0031](0031-plusieurs-workflows-par-projet.md) | A project runs several workflows; each work item is pinned to the version it was born in; typed edits are text grafts (amends 0023) | accepted |
| [0032](0032-sections-d-administration-par-manifeste.md) | Administration sections are declared by manifest; the console renders them, no plugin code runs in it | accepted |
| [0033](0033-registre-d-agents-et-bibliotheque-de-skills.md) | Agents are registered, versioned objects (internal and external, rights intersected with the human's); skills are a library they carry, declaring no permission | accepted |
| [0034](0034-connecteurs-par-capacites.md) | Connectors by capability, a policy per operation; a discovered MCP tool is born closed (amends 0014) | accepted |
| [0035](0035-actions-gouvernees-dans-le-coeur.md) | Governed actions move into the core and run in Temporal; each effect is recorded by key and compensated on failure | accepted |
| [0036](0036-un-gabarit-livre-ce-qu-un-greffon-installe.md) | A template ships what a plugin installs (`defaults.extensions`); without the plugin the project is born without it, and the audit says so | accepted |
