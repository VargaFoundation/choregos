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
| [0001](0001-frozen-contracts.md) | Contracts are frozen at M0 and versioned | accepted |
| [0002](0002-acp-as-the-agent-contract.md) | ACP as the agent contract; OpenHands default superseded by 0011 | accepted |
| [0003](0003-temporal-as-the-orchestrator.md) | Temporal for durable orchestration | accepted |
| [0004](0004-cost-counted-at-the-gateway.md) | Cost is counted at the gateway, not by the agent | accepted |
| [0005](0005-adapters-and-fakes.md) | Every connector has an interface and a fake | accepted |
| [0006](0006-three-production-locks.md) | Three independent locks protect production | accepted |
| [0007](0007-optional-memory.md) | Memory must earn its place before anything depends on it | accepted |
| [0008](0008-idempotence-and-run-id.md) | Deterministic identifiers and idempotence everywhere | accepted |
| [0009](0009-gitops-single-source-of-truth.md) | The cluster changes only through Git | accepted |
| [0010](0010-gates-are-mechanisms.md) | A guarantee is a mechanism, never a prompt | accepted |
| [0011](0011-openhands-removed.md) | OpenHands removed; `claude-code` becomes the default | accepted |
| [0012](0012-the-engine-is-not-tied-to-software.md) | The engine is not tied to software | accepted |
| [0013](0013-agent-task-density.md) | Agent task density, and what we take from AX | accepted |
| [0014](0014-a-platform-held-tool-catalogue.md) | A platform-held tool catalogue | accepted |
| [0015](0015-ai-authorship-attestation.md) | A signed "AI authorship" attestation per merged change | proposed |
| [0016](0016-agent-sandbox-executor.md) | An `agent-sandbox` executor: warm pools, suspend across a human gate, snapshots | accepted |
| [0017](0017-mcp-2026-07-28-conformance.md) | MCP 2026-07-28: stateless tools, tasks, elicitation, resource-bound OAuth | proposed |
| [0018](0018-otel-genai.md) | OpenTelemetry GenAI spans from the runner and the gateway; the ledger stays the truth for money | proposed |
| [0019](0019-temporal-workflow-streams.md) | Temporal Workflow Streams as the transport of a run's live journal | proposed |
| [0020](0020-trajectory-evals.md) | Trajectory evals in the nightly matrix, scored from the journal by mechanisms | proposed |
| [0021](0021-spiffe-behind-the-run-token.md) | SPIFFE identities behind the run token: revocation by deletion, mTLS to the gateway | proposed |
| [0022](0022-a-federable-catalogue.md) | A federable catalogue: registry entries become pull requests, born closed | proposed |
| [0023](0023-a-visual-workflow-builder.md) | A visual builder: read-only map first, then edits that are YAML diffs | proposed |
| [0024](0024-two-editions.md) | Two editions: a single-organisation community core, a separate enterprise layer | accepted |
| [0025](0025-the-engine-drives-platform-it.md) | The engine drives platform IT: healthcheck findings become governed changes, nodes last | accepted |
| [0026](0026-what-the-core-rls-covers.md) | What the core's RLS covers — every table checked; identity included since 0.11 (update of 2026-09-29) | accepted |
| [0027](0027-noisy-neighbours-at-admission-and-quota.md) | Noisy neighbours: bounded at admission and by a per-organisation namespace quota, not by a Temporal queue per organisation | accepted |
| [0028](0028-the-governed-execution-of-change.md) | Choregos governs the execution of change, not agents in general; superseded by 0029 | superseded |
| [0029](0029-a-platform-for-governed-agents.md) | Choregos is a general-purpose platform for governed agents: several workflows per project, an agent registry, skills, MCP, connectors by capability | accepted |
| [0030](0030-an-mcp-door-for-external-clients.md) | A door for external MCP clients inside the API: `/mcp`, scoped tokens first, OAuth next, no decision through MCP | accepted |
| [0031](0031-several-workflows-per-project.md) | A project runs several workflows; each work item is pinned to the version it was born in; typed edits are text grafts (amends 0023) | accepted |
| [0032](0032-admin-sections-by-manifest.md) | Administration sections are declared by manifest; the console renders them, no plugin code runs in it | accepted |
| [0033](0033-agent-registry-and-skill-library.md) | Agents are registered, versioned objects (internal and external, rights intersected with the human's); skills are a library they carry, declaring no permission | accepted |
| [0034](0034-connectors-by-capability.md) | Connectors by capability, a policy per operation; a discovered MCP tool is born closed (amends 0014) | accepted |
| [0035](0035-governed-actions-in-the-core.md) | Governed actions move into the core and run in Temporal; each effect is recorded by key and compensated on failure | accepted |
| [0036](0036-a-template-ships-what-a-plugin-installs.md) | A template ships what a plugin installs (`defaults.extensions`); without the plugin the project is born without it, and the audit says so | accepted |
| [0037](0037-a-workflow-s-effects-are-written.md) | What a workflow does is written (`does`, `production`), not carried by a state's name; names are still read for older workflows, and a rename writes the effect down first | accepted |
| [0038](0038-a-bundled-yaml-editor.md) | The console's YAML editor is bundled CodeMirror, its workflow map is plain HTML; nothing is fetched from a third party at run time | accepted |
| [0039](0039-everything-a-user-reads-is-in-english.md) | Everything a user reads is in English; identifiers and machine codes do not change, code and `docs/plan` stay in French (§2-3 superseded by 0046) | accepted |
| [0040](0040-a-built-in-agent-catalogue.md) | A built-in agent catalogue — one agent per role, a study's researcher and architect, the MCP clients; installed on demand or when a workflow names one, never rewritten (amends 0033) | accepted |
| [0041](0041-approval-follows-what-the-batch-ships.md) | The release approval follows what the batch ships (the policy, or a work item whose workflow names an approver); the train tells each work item it has shipped or rolled back; the webhook boards the train the workflow takes (amends 0006) | accepted |
| [0042](0042-the-interface-s-letter-case.md) | What the console names is lowercase (the design system); what it says is a sentence, with a capital; data keeps its case (amends 0039); superseded by 0048 | superseded |
| [0043](0043-the-console-s-look.md) | The console looks like an operations console: the design system's tokens remapped to Radix Colors with a contrast budget per mode, Geist to read and Geist Mono to compare, a theme the person chooses applied before the first paint (amends 0042); §1, §6 and §7 superseded by 0048 | accepted |
| [0044](0044-people-are-declared-in-the-workflow.md) | People are declared in the workflow: the policy's `approvals.*` and `review.require_human_for_risk` are accepted but not enforced, and saying so is logged; the presets no longer write them | accepted |
| [0045](0045-the-provenance-of-a-signal.md) | Every signal says where it comes from — declared by the agent, observed by the platform, inferred, or unknown; `Evidence.measured` carries it, and a run is not shown done on its agent's word alone | accepted |
| [0046](0046-english-everywhere.md) | English everywhere: identifiers, code, comments, `docs/plan`, ADRs and commit messages too; patch ids frozen, Temporal names aliased, contracts via `contract-change`; a guard refuses French in new lines (supersedes 0039 §2-3) | accepted |
| [0047](0047-a-budget-is-a-reservation.md) | A budget is a reservation: a run's key is minted at what is left (stage, agent, ticket, project day, organisation month); a gateway refusal stops the stage, never retries it; the ledger is the counter; more money is an audited decision | accepted |
| [0048](0048-the-console-owns-its-primitives.md) | The console owns its primitives: it looks like Temporal's UI, writes in sentence case, uses sans for what is read and mono for what is compared, rounds at 4–6 px, and no longer depends on the Varga Foundation design system (supersedes 0042, and 0043 §1, §6, §7) | accepted |
| [0049](0049-an-artifact-is-typed-stored-and-traced.md) | An artifact is typed, stored and traced: a revision of a named output with a type, a media type, a sha256 checked on every read and the run that produced it; inline values in the database, files in the object store, which only the API touches | accepted |
| [0050](0050-skill-supply-chain-gate.md) | Skills come from a pinned commit of an allowlisted github.com or gitlab.com repository, recording repository, SHA, folder and licence; an update is a proposal a person applies (amends 0033) | accepted |
| [0051](0051-knowledge-bases.md) | Knowledge bases are curated corpora of the organisation, apart from memory: uploaded, converted to markdown in a child process outside the request, scoped to the org, projects or groups, served as read-only files or a hybrid (full-text + pgvector) index with citations; synced from SharePoint/OneDrive (Graph, `Sites.Selected`), a web site or git, a document deleted at the source leaving the index | accepted |
| [0052](0052-netops-connectors.md) | Netops connectors: pfSense (REST API package), Cilium (read-only service account) and Hubble (the `hubble` CLI against Relay), nginx (from the GitOps repository, `nginx -t` in the runner); reads through the broker, TLS never turned off silently, writes only as governed actions or pull requests | accepted |
| [0053](0053-agent-sandbox-as-built.md) | The `agent_sandbox` executor as built: the run token is pushed sealed to the claimed pod's own key (read through the API server), not mounted; the queue is the executor's, not a waiting claim; `snapshot` means a sandbox never used before, and needs no method; a question suspends the sandbox and the answer resumes the same run, bounded by `sandbox.suspend_max_hours` (amends 0016) | accepted |
| [0054](0054-a-run-keeps-its-pod-log-tail.md) | At the end of every run the orchestrator archives, through the `Executor` protocol only and before any cancel, the termination reason and the last 200 lines of the pod log, redacted, once per run | accepted |
| [0055](0055-fewer-tokens-per-run.md) | Fewer tokens per run: rtk filters shell output, headroom compresses tool results inside the runner pod on loopback, behind LiteLLM; off by default, a deployment kill switch, a policy and a stage switch; default-on per backend only once the eval matrix shows a lower cost and no loss of success | accepted |
| [0056](0056-a-deterministic-step.md) | A deterministic step: the `job` actor runs an argv in a platform-known image, with no model and no gateway key; its exit code is its status and its facts are measured | accepted |
| [0057](0057-fan-out-and-join.md) | Fan-out and join: a system transition opens one child work item per element of a list, idempotently by key, and passes once every child is settled | accepted |
| [0058](0058-connector-secrets-are-write-only-values.md) | Connector secrets are write-only values: typed in the console, sealed in the database (AES-256-GCM envelope, KEK from the deployment, derived `store:` references), never returned, redacted, rotated by `rewrap` (amends 0034) | accepted |
| [0059](0059-a-work-item-filed-in-choregos-is-born-in-its-tracker.md) | A work item filed from the console or MCP on a project with an external tracker is first created as an issue there (`source:choregos`, never `agent-ready`, fields validated beforehand), then recorded under the issue's key; a refusing tracker gives a 502 and nothing is created | accepted |
| [0060](0060-a-gateway-agnostic-model-catalogue.md) | A gateway-agnostic model catalogue: gateway capabilities, an OpenAI-compatible adapter, an organisation catalogue (enabled per model, role and usage; price override; tiers) in `open` or `curated` mode, refused at publication and at run time, never bypassed by `allow_unvalidated` | accepted |
| [0061](0061-agent-ownership-scopes-and-forks.md) | Agent ownership scopes and forks: common (platform), organisation, project and personal agents; one server-side permission matrix with the reason for each refusal; a personal agent never runs in a workflow and a project agent only in its project; a fork copies a version and reports when its upstream moved, never following it; existing agents migrated (catalogue → platform, others → org) (amends 0033, 0040) | accepted |
| [0062](0062-remote-agents-over-a2a.md) | Remote agents over A2A: `Agent.kind = remote` with an `a2a` runtime, the card pinned at publication, the call made from the runner pod (card re-read, drift refused), a user-delegated OAuth credential (DCR + PKCE) whose refresh token stays sealed and only a short-lived access token reaches the run's Secret, declared cost booked as `external`, the same gates on the outputs | accepted |
| [0063](0063-agents-written-in-code.md) | Agents written in code: an ACP agent in an operator-named, digest-pinned image built from the runner, run by the `code` backend; its version says `runtime: {kind: code, image, command}` | accepted |
| [0064](0064-a-theme-is-data-the-console-applies.md) | A theme is data the console applies: `GET /api/v1/theme` serves the community default or a plugin's theme (`declare_theme_provider`), validated against the console's contrast budget and replaced by the default when invalid; colours, radii, fonts, logo, name and footer are data, the brand is never in the community edition (amends 0048 §6) | accepted |
| [0065](0065-catalogues-of-mcp-servers-and-skills.md) | Catalogues of MCP servers and skills: a curated seed resolved by a replayable generator into a versioned file in the image, installed through the existing gates (connector, skill gate), never enabled by default; amends 0022 and 0050 | accepted |
| [0070](0070-network-access-is-bounded-and-enforced-per-run.md) | Network access is bounded by the organisation, narrowed by the project and the agent, and enforced per run: one host-pattern grammar (`choregos_core/network.py`, deny wins, IP literals refused), `PolicyCeiling.network` and `AgentSpec.network` that only narrow, an effective network per run read by the egress proxy with the run token, cumulative decisions in `network_decisions`; each layer is named as wall or net | accepted |
| [0071](0071-an-agent-version-reaches-production-through-channels.md) | An agent version reaches production through channels: a revision is an immutable `agent_versions` row; two pointers on the agent (production, testing) and a per-project switch select it; a deterministic canary share per work item; a per-agent policy says what saving a revision does (default: it goes to production, as before); production needs the eval suite, the candidate's record, a human session with a fresh sign-in and the plugin gesture `agent.release`; rollback to a revision that was in production skips the gates; every move is a row of `agent_releases` (amends 0033, 0061) | accepted |
| [0072](0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md) | The runtime of a real deployment is agent-sandbox: the chart and the code default to it, Tekton is chosen by name, the API receives the executor and refuses a runtime the deployment does not run, the chart fails at render without the operator (supersedes in part 0016 and 0013) | accepted |
| [0073](0073-github-app-installations-are-discovered-not-typed.md) | GitHub App installations are discovered, not typed: no `installation_id` in any form; installations are polled, installed from the console through the App's Setup URL with a signed state, and claimed per organisation (implicit on a single-organisation instance, OAuth proof otherwise); connectors, adapters and the runner's git token use the claimed installation, injected server-side (amends 0034) | accepted |
| [0074](0074-catalogue-rights.md) | Catalogue rights: one server function decides who sees, installs, configures and enables an MCP server or a connector; an organisation connector is available to all projects or only to selected ones, enforced in the broker and the project policy; amends 0014, 0034 and 0065 | accepted |
| [0075](0075-an-agent-reads-several-memories.md) | An agent reads several memories, global and project, and writes to one: named memories (global with a reach, project owned by its project) beside the implicit shared and own ones; an ordered list of at most eight bindings on the version, one write target (proposals only when it is global); the legacy `memory` derives the list so no version or checksum is rewritten; recall merges the memories by precedence, deduplicated by subject then round-robin to the budget; `memory_facts` carry their organisation and store (amends the S32-02 behaviour, keeps 0007) | accepted |
| [0076](0076-an-agent-s-history.md) | An agent's history: a version carries a message and its restore origin, publishing is from a base (409 `stale_base`, `no_change`), a restore appends a byte-for-byte copy, the console reads one version at a time with History, diff and restore | accepted |
