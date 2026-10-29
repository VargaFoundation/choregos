# 0111 — A workflow catalogue: entries from the core, templates, plugins and the organisation, whose agents resolve in the registry

- **Status**: accepted, 2026-10-28, by the product owner ("a catalogue of workflows that reference
  agents from internal or external catalogues"; sharing to the organisation's catalogue is `org_admin`'s)
- **Concerns**: a new route `GET /orgs/{org}/workflow-catalogue` and its DTO (contract-change),
  `POST /projects/{id}/workflow-adoptions`, the optional `metadata.catalogue` block of
  `workflow.schema.json` (contract-change), `workflow_defs.source` and `origin`, the table
  `org_workflows`, the readiness item `workflow-catalogue`, the console's
  `/p/<slug>/workflows/catalogue` and the project wizard; builds on
  [ADR 0031](0031-several-workflows-per-project.md), [ADR 0036](0036-a-template-ships-what-a-plugin-installs.md),
  [ADR 0040](0040-a-built-in-agent-catalogue.md), [ADR 0062](0062-remote-agents-over-a2a.md),
  [ADR 0074](0074-catalogue-rights.md) and [ADR 0093](0093-an-agent-ships-as-a-plugin.md)

## Context

What a person can start a workflow from, surveyed on `main` at `0a29b5b`:

- **"New workflow"** (`/p/<slug>/workflows/new`) lists `GET /workflows/templates`: the three core
  workflows of `TEMPLATE_NAMES`, all of which deliver code.
- **A template's workflows** (`joiners-leavers`, `investment-research`, a plugin's templates) are only
  reachable by creating a *project* from that template. A second project that wants the onboarding
  workflow cannot take it.
- **A workflow written in one project** cannot be reused in another except by copying its YAML.
- **Agents**, on the other hand, already have catalogues: the organisation's registry (ADR 0033), the
  built-in entries now shipped as plugins (ADR 0040, 0093), plugins from other signed sources (ADR
  0093), and remote agents over A2A (ADR 0062). A workflow names one with `agent: <slug>[@N]`, and
  publishing a workflow that names a catalogue agent installs it (ADR 0040 §5).

Workflows are the only thing the platform runs that has no catalogue. The ADR 0040 review made the same
finding about agents: what cannot be found is not reused.

## Decision

1. **No new package format.** A catalogue entry is a workflow YAML, versioned by its
   `metadata.version`, with an optional `metadata.catalogue` block (`title`, `summary`, `tags` — e.g.
   `hr`, `software`, `research`) that the validator checks and the runtime ignores. Entries come from
   four **sources**, listed together:
   - `core` — `choregos_core/dsl/templates/` (`default-simple`, `default-request` of ADR 0110, …);
   - `template:<name>` — every workflow of the deployment's templates;
   - `plugin:<plugin>` — every workflow of an installed plugin's templates (ADR 0093). **External
     catalogues are plugin sources**: a workflow from outside arrives signed, pinned and through the
     supply-chain gate, like an agent. There is no "import a workflow from a URL";
   - `org` — workflows the organisation shares from its own projects (decision 4).
2. **What an entry requires is computed, never declared.** The listing (`GET /orgs/{org}/workflow-catalogue`)
   returns for each entry its source and version, the capabilities `exigences` derives (so the console
   can say "needs a repository" or "needs no repository" and filter on it), the human groups it
   declares (ADR 0044), and **each agent it names with where it resolves**, in the order of ADR 0040 §4:
   the organisation's own agent, an installed plugin's agent, a remote agent (ADR 0062) — or
   **unresolved**, with the plugin of a configured source that ships it when one does.
3. **Adopting copies, then installs.** `POST /projects/{id}/workflow-adoptions {source, name, version?, as?, make_default?}`
   publishes the entry's YAML as a new workflow of the project (renamed by `metadata.name` when `as` is
   given; a name taken by another workflow is a 409, as `create_only` already does, and adopting the
   same entry again publishes that workflow's next version), records it as `source: catalogue` with its
   `origin` (`<source>/<name>@<version>`; `source` stays an enumeration), and
   installs the agents it names under ADR 0040 §5. It **never installs a plugin implicitly**: an
   unresolved agent refuses the adoption with the plugin to install and who may install it (ADR 0074).
   Before adopting, the console shows the target project's readiness *as it would be* (the entry's
   requirements against the project's connectors).
4. **An organisation shares its own workflows.** A project workflow version can be **shared to the
   catalogue** (`org` source) by an `org_admin` only, the right ADR 0074 gives for catalogue writes; the entry
   is a copy of that version, so later edits in the project do not leak into it until shared again.
5. **Updates are offered, never applied.** A project's adopted workflow whose source now has a newer
   version turns a readiness item of its own (`workflow-catalogue`) to *check* — the same for an entry the catalogue
   no longer offers; adopting the update publishes the project's next version, and work
   items keep the version they were born in (ADR 0031 §2).
6. **The reference syntax does not change.** `agent: <slug>[@N]` stays the only way a workflow names an
   agent; no agent is defined inline in a workflow. A remote or plugin agent is an organisation registry
   agent like any other, so an entry that runs in one organisation runs in another once the same agents
   resolve there.
7. **The wizard offers the catalogue.** Creating a project chooses a template *or* a catalogue workflow;
   without a choice, ADR 0110 decides.

## Consequences

- One listing answers "which processes can I run here, and what would each need?" across trades.
- The shipped templates' workflows become reusable outside their template without copying files.
- A plugin can ship a workflow and the agents it names together, and an organisation can adopt the
  workflow without adopting the template's project defaults.
- Contract changes: the catalogue DTO, the adopt request, `metadata.catalogue`; each in its own
  `contract-change` PR (ADR 0001).
- Stories S49-04 to S49-08 in `docs/plan/08-backlog.yaml`.

## Alternatives discarded

- **A workflow registry with its own lifecycle** (draft, deprecated, retired), as agents have. A
  workflow is already versioned per project and pinned per work item; a second lifecycle would have to
  agree with it. Sharing a version as a copy is enough.
- **Link instead of copy** (a project runs the catalogue's workflow by reference). A catalogue update
  would then move every project at once — exactly what ADR 0040 §4 refuses for agents.
- **Import a workflow from any URL or registry.** A workflow names agents and tools; taking one from
  outside without the plugin chain of trust is the supply-chain path ADR 0022 and 0093 close.
- **Installing the missing plugins during adoption.** Installing a plugin is an organisation-wide,
  separately approved gesture (ADR 0093, 0074); hiding it inside a project gesture would bypass that.

## Implementation — 2026-10-28 (S49-04 to S49-08, S49-05b, S49-07b)

Delivered: the contract, the listing over the four sources, adoption (renaming keeps every other byte),
sharing by an `org_admin`, the readiness item, the console's catalogue page and the wizard's choice.
S49-05b: an `unresolved` agent names the plugin of a configured source that ships it. A plugin index's
release may list the `agents` it ships (`plugin-index.schema.json`, written by `choregos plugin index`);
the sources' indexes are read at most once a minute, the offline directory from its plugins, and a source
that cannot be read says nothing. An index without the field names no plugin. S49-07b: sharing from the
console, shown only where the project's workflow list says `can_share` (ADR 0074).
