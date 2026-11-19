# STATUS

State of the stories of the backlog (`08-backlog.yaml`), kept by the integrator stream. This page
is an index: one line per stream. Each story's row, with its status, its PR and what it proves and
does not prove, is in its stream's file under [`status/`](status/). The narrative this page carried
until 2026-11-17 (the first CI, the 2026-09-24 bench, what was missing to say "in production") is
in [status/history.md](status/history.md).

Legend: ✅ delivered and tested · 🟡 partially delivered (the rest is stated) · ❌ announced and wrong · ⬜ not started.

**2026-09-24 — stocktaking.** Three audits cross-checked against the real bench showed that several ✅
of this table were wrong; they are corrected below, and the detail is in
[STATE-OF-THE-PROJECT-2026-09-24.md](STATE-OF-THE-PROJECT-2026-09-24.md), with the P0 → P3 plan
that follows from it. Rule since that day: nothing is ✅ without a test that fails in its absence.

## Where a new row goes

1. A story's row goes in its stream's file, `docs/plan/status/<stream>.md` (`S80-03` goes in
   `status/S80.md`), as one table row `| S80-03 | S80 | 🟡 | #1234 | … |`: the story id, the
   stream, one status of the legend, the PR, then what the PR proves and what it does not prove.
   Never in this index: it holds no story row.
2. A new stream: create `status/<stream>.md` with the header and the table of the other files, and
   add one line for it to the table below, under 300 characters.
3. A pull request that is not a backlog story goes in `status/outside-backlog.md` (stream `—`).
4. Every story of `08-backlog.yaml` has exactly one row across the status files, and a row sits in
   the file of its stream. `tests/docs/test_status_layout.py` refuses anything else, and this page
   over 300 lines or with a line over 300 characters.

**Total**: counted from the status files, not written by hand: `uv run python tools/status_total.py`.

## Streams

| Stream | What it is about |
|:--|:--|
| [S0](status/S0.md) | Contracts and foundation |
| [S1](status/S1.md) | Temporal orchestrator |
| [S2](status/S2.md) | Runner, ACP agents, Tekton |
| [S3](status/S3.md) | GitHub connectors |
| [S4](status/S4.md) | Gateway and costs |
| [S5](status/S5.md) | Front end |
| [S6](status/S6.md) | API |
| [S7](status/S7.md) | Kubernetes platform and GitOps |
| [S8](status/S8.md) | Templates and provisioning |
| [S9](status/S9.md) | Release train and CD |
| [S10](status/S10.md) | Findings and memory (Choregos) |
| [S11](status/S11.md) | Ecphoria (repository VargaFoundation/ecphoria) |
| [S12](status/S12.md) | Evals & conformance |
| [S13](status/S13.md) | Multi-backend & second ecosystem |
| [S14](status/S14.md) | Governed change on a data platform (ADR 0028) |
| [S15](status/S15.md) | MCP door for external clients and the Integrations page (ADR 0029, 0030) |
| [S16](status/S16.md) | Several workflows per project, readable and editable (ADR 0031) |
| [S17](status/S17.md) | Administration by manifests and enterprise edition on the dev (ADR 0032) |
| [S18](status/S18.md) | Agent registry and skill library (ADR 0033) |
| [S19](status/S19.md) | Connectors by capability and MCP servers (ADR 0034) |
| [S20](status/S20.md) | Governed actions in the core and the HR joiners/leavers scenario (ADR 0035) |
| [S21](status/S21.md) | Usability: readable console, English everywhere, agent catalogue, enforced governance, development project (product review of 2026-10-07) |
| [S22](status/S22.md) | A ticket's journey, agents in English, the dev tenant (review of 2026-10-08) |
| [S23](status/S23.md) | The console's UX audit of 2026-10-08: width, inbox, project setup |
| [S24](status/S24.md) | The look of an operations console: softened contrast, Geist, chosen theme, sidebar (ADR 0043) |
| [S25](status/S25.md) | What the agent does, and how we know it: plan, activity, provenance, receipts between steps (ADR 0045) |
| [S26](status/S26.md) | P0 fixes: train status, problem+json, train metrics, members read check, train environments (plan 2026-10-12) |
| [S27](status/S27.md) | Budgets that hold: reservation, ledger-backed spend, org ceiling, enforced guardrails (plan 2026-10-12) |
| [S28](status/S28.md) | One-command local install: docker compose up, real key, tested in CI (plan 2026-10-12) |
| [S29](status/S29.md) | Spend screen by agent, workflow, work item, project (plan 2026-10-12) |
| [S30](status/S30.md) | Console: Temporal-like look without the Varga design system, scope-switching sidebar, agents and members pages (plan 2026-10-12) |
| [S31](status/S31.md) | Typed, stored, traced artifacts between agents (plan 2026-10-12) |
| [S32](status/S32.md) | Agent builder, lifecycle, skills import behind a supply-chain gate (plan 2026-10-12) |
| [S33](status/S33.md) | Knowledge bases and specialist agents, netops included (plan 2026-10-12) |
| [S34](status/S34.md) | English everywhere: code, identifiers, docs, commits (plan 2026-10-12) |
| [S35](status/S35.md) | The agent_sandbox executor: warm pool, suspend across a human gate, proved on the dev cluster (ADR 0016, plan 2026-10-12) |
| [S36](status/S36.md) | Agent logs: transcript stored, pod logs and termination reason archived, a logs tab (plan 2026-10-12) |
| [S37](status/S37.md) | Fewer tokens per run: rtk and headroom, measured, switchable at every level (ADR 0055) |
| [S38](status/S38.md) | Investment research: deterministic steps, market data, gates on measured numbers, a campaign template (plan 2026-10-14) |
| [S39](status/S39.md) | The enterprise edition in production, governed agents, catalogues (PLAN-2026-10-15) |
| [S40](status/S40.md) | Governed network access, an agent's lifecycle, catalogues with rights (PLAN-2026-10-16) |
| [S41](status/S41.md) | Prompt, voice and hosted agents (ADR 0080), Kyverno and CI findings |
| [S42](status/S42.md) | Knowledge bases: rights, distillation, sources; plugin administration sections |
| [S43](status/S43.md) | Agents ship as plugins; ServiceNow is the first (PLAN-2026-10-27-servicenow) |
| [S44](status/S44.md) | Trials and confinement: Graphify, SkillSpector, the kernel confines the agent |
| [S45](status/S45.md) | English everywhere, finished (plan 2026-10-27, ADR 0098) |
| [S46](status/S46.md) | Consuming an external MCP gateway (plan 2026-10-27, ADR 0105) |
| [S47](status/S47.md) | Hardening and gaps from the 2026-10-27 audit (ADRs 0099-0104) |
| [S48](status/S48.md) | A workflow chooses its next step; an identity provider per organisation |
| [S49](status/S49.md) | Workflows for every trade: a default without a repository, a catalogue, a map that edits everything (ADRs 0110-0112) |
| [S50](status/S50.md) | An agent acts under an identity: assigned, own, on behalf of a person; child agents (ADR 0107) |
| [S51](status/S51.md) | Knowledge, tracker context and workspaces reach the agents |
| [S52](status/S52.md) | A decision is a step, answered by a decision model (ADR 0121, 0123) |
| [S55](status/S55.md) | A console that makes a complex product simple: colour that orients, readable pages (ADR 0120) |
| [S56](status/S56.md) | A workflow starts from a trigger: tracker, HTTP, schedule or a person, one audit (ADR 0113) |
| [S57](status/S57.md) | An agent calls its run's tools from a script: the runner relays, the API governs each call (ADR 0122) |
| [S58](status/S58.md) | A project may hold several repositories (ADR 0124) |
| [S59](status/S59.md) | Findings of the first production tickets: checks, plugins, knowledge, live pages |
| [S60](status/S60.md) | An agent is a closed set: instructions, model, tools, knowledge, memory (ADR 0127) |
| [S61](status/S61.md) | An artifact says how it is shown (ADR 0129) |
| [S62](status/S62.md) | The A2A door, the CLI binaries and the public user documentation |
| [S63](status/S63.md) | Assistants: a plugin ships one, an organisation installs it for an audience, a person converses with their own identity (ADRs 0133-0140) |
| [S64](status/S64.md) | What is running, what it spends, and stopping it (ADRs 0141-0142) |
| [S65](status/S65.md) | Outposts: tools that live where the platform cannot reach, behind a place that only dials out (ADRs 0147-0149) |
| [S66](status/S66.md) | Assistants that launch projects, groups and member profiles (ADRs 0158-0162) |
| [S67](status/S67.md) | The terminal is a surface of the platform (ADR 0154) |
| [S68](status/S68.md) | The editor is a surface of the platform: a code editor extension that converses, shows what runs and what waits, and runs nothing locally (ADR 0155) |
| [S69](status/S69.md) | The publishing identity is one file; the core names no operator (ADRs 0156-0157) |
| [S70](status/S70.md) | Sign-in security in the core, OAuth connectors, the GitHub App from a manifest |
| [S71](status/S71.md) | Decisions D14 and D5 of the graal and Choregos offer, in the documentation |
| [S72](status/S72.md) | The Open Knowledge Format at the edge of a knowledge base (ADR 0174) |
| [S73](status/S73.md) | The publication moves to graalsystems |
| [S74](status/S74.md) | An organisation may own a host (ADR 0175) |
| [S75](status/S75.md) | The core is named Tranibor: visible name, packages, images and charts, environment variables (ADR 0176) |
| [S76](status/S76.md) | The corpus's own structure is a retrieval key: outlines, section reading, PDF bookmarks (ADR 0051) |
| [S80](status/S80.md) | From engine to product: the plan of 2026-11-16 |
| [S81](status/S81.md) | The phone is a surface of the platform: an Android app for agents, assistants and voice (ADRs 0192-0194) |
| [S83](status/S83.md) | The contracts speak no trade: neutral names, software delivery and investment research as plugins, ontology removed (ADR 0181) |
| [owner review](status/owner-review.md) | The owner review of 2026-11-03 |
| [—](status/outside-backlog.md) | Rows that are not backlog stories, named by their pull request |
