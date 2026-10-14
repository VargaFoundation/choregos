# 0012 — The engine is not tied to software

- **Status**: accepted, 2026-09-23
- **Concerns**: the core (DSL, interpreter, gates, playbooks) and what depends on it

## Context

Choregos was born for software delivery: a ticket goes in, a production release comes out.
The question asked is a different one — can the same core carry **HR staffing**,
**administrative case handling**, or any line of work where requests arrive, are processed
in stages, and someone has to answer for what was done?

This is not a matter of wishing but of inventory: what, in the machine, really speaks of
software? The `demo/` answers in practice — two projects, the same deployment, one writing
code and the other qualifying candidate profiles.

## What does NOT speak of software, and is the essential part

| Mechanism | Why it is generic |
|---|---|
| The DSL (states, transitions, actors, `on_fail`, deadlines) | A graph of states named by the business; nothing in it assumes a repository |
| The Temporal interpreter | One workflow per request, resumed after failure, with pause, resume and definition migration |
| The `agent` / `human` / `system` actors | A human approval with an SLA is a need of every line of work |
| Budgets (turns, minutes, euros) and one key per run | What bounds an agent's spend does not depend on what it produces |
| Memory (Ecphoria) | Facts, decisions, episodes: nothing code-specific |
| Audit, cost per stage, resumption, evals | Likewise |

## What spoke of software, and what was done about it

| Hard point | Decision |
|---|---|
| **Playbooks** lived in the package, for ten development roles | `CHOREGOS_PLAYBOOKS_DIR`: the deployment brings its roles (`<role>.md`), read **before** the package's. A ConfigMap is enough, the chart mounts it |
| The only gate usable outside code was `evidence_present`, which requires **tests** | New gate `outputs_present`: the stage produced what the transition declares (`outputs:`). Mechanical, not declarative — which is what separates a guarantee from an instruction |
| The **tracker** had to be external (GitHub, Jira) | Connector `tracker: internal`: the platform holds the ticket, external writes are honest no-ops |
| The **runner** clones a repository and works in a git workspace | Unchanged, and that is a limit: a line of work without a repository keeps a dummy `repo`. See below |

## What remains tied to software, to know before committing

1. ~~**`ProjectConfig.repo` is mandatory.**~~ **Lifted on 2026-09-24.** `repo` is optional:
   without it the runner prepares an empty directory tracked by a **local** git (which still
   lets it measure what the agent wrote), clones nothing, pushes nothing, and the gates that
   read a diff refuse for lack of material. The demo's HR project has no repository at all —
   that is the proof, not the intention. What stays true: a transition that assumes an SCM
   (open a PR, read checks) fails with a message that names the cause, rather than
   setting off on an empty URL.
2. **`StageOutputs` carries development fields** (`allowed_paths`, `spec_markdown`,
   `verdict`…). The model tolerates extra fields, so a line of work names its outputs
   freely — but those are not typed, and the gate can only check their presence, not their
   shape.
3. ~~**`Evidence` speaks of tests, lint and coverage.**~~ **Lifted on 2026-09-24.**
   `Evidence.facts` carries facts named by the business, and the `evidence_facts` gate
   reads them — presence, minimum threshold, boolean true. The front shows them in place of
   software measurements when they are what exists.
4. ~~**DSL roles are a closed enumeration.**~~ **Lifted on 2026-09-24.** A role is a free
   identifier: `sourcing`, `qualification`, `instruction_dossier`. The package's roles keep
   their meaning — the platform relies on `implement`, `review` and `verify` for its
   measurements and for cross review — and the playbook already resolved by role name,
   which made opening it almost free. The validator **warns** (it does not forbid) when no
   playbook resolves for a role: the deployment has to bring it, and the absence was
   otherwise discovered on the first ticket.
5. **Software gates remain numerous** (`diff_size_max`, `ci_green`, `scans_ok`…). They do
   not get in the way of another line of work, which does not declare them; but the balance
   shows where the platform grew up.

## Decision

The core is **declared generic**, and the four mechanisms above made it usable as-is for a
non-software line of work. The five limits are written here rather than discovered by the
first person to try. The two steps announced here — a project **without a repository** and
**business-named evidence** — were taken on 2026-09-24; the two remaining limits block no
line of work, they show where the platform grew up.

## Consequences

- `demo/workflows/staffing.yaml` runs on the same deployment as the software demo, without
  a single code change between the two.
- A deployment can replace **any** package playbook, including `implement`: adapting a role
  to a house does not require modifying Choregos.
- The `outputs_present` gate gives every workflow a mechanical guarantee, which avoids the
  natural slide into "we trust the agent" when there are no tests.
