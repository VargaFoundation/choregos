# Usage

This page takes a project from nothing to a ticket that an agent carries through to the end.

## 1. Try it without installing anything

```bash
make setup     # uv sync + pnpm install
make demo      # one ticket travels the whole platform, in memory, no cluster
make ci        # lint, strict typing, the test suite, contracts, charts
```

`make demo` uses the `fake` connectors. It proves the machinery, not the integrations.

## 2. Create a project

Three ways, same result: the web wizard (`/projects/new`), the CLI, or the API.

```bash
choregos login --api-url https://api.example --token "$CHOREGOS_TOKEN" --org acme
choregos projects create billing-api --name "Billing API" --org acme
choregos projects list
```

`--template <name>@<version>` on `create` also provisions the project: repository, board,
GitOps manifests and scaffolding. Without it, the project is created empty and you attach
its connectors yourself, from the UI (*Settings → connectors*: type and configuration, secrets referenced never typed) or the API (`PUT /projects/{id}/connectors/{kind}`).

A project needs, at minimum: a **tracker** (where humans look), a **workflow** and a
**policy**. Everything else has a default or can be added later.

### Connectors

| Kind | Implementations |
| :-- | :-- |
| `tracker` | `github-issues`, `jira`, `gitlab-issues`, `internal`, `fake` |
| `scm` | `github`, `fake` |
| `ci` / `cd` | `tekton` / `argocd`, `demo`, `fake` |
| `runtime` | `tekton`, `k8s_job`, `aca`, `local_docker`, `agent_sandbox`, `fake` |
| `gateway` | `litellm`, `direct`, `fake` |
| `memory` | `ecphoria`, `lexical`, `fake` |
| `notify` | `slack`, `fake` |

A project only needs what its workflows read ([ADR 0034](adr/0034-connectors-by-capability.md)).
`GET /projects/{id}/requirements` derives it — a guarantee says what it reads (`scope_respected` a
diff; `ci_green` and `scans_ok` the pull request's checks, read through the `scm`, so GitHub Actions
needs no `ci` connector), an agent whose role works in a repository needs an `scm`, a release
train or a production state (`production: true`) needs a `cd`, a transition that opens or merges a
pull request (`does`) needs an `scm` — and says why for each. An HR project shows
neither `scm`, nor `ci`, nor `cd`; the settings page lists what is required, what is configured,
and *add a connector* for the rest. `GET /connectors/types` reads the adapter registry, plugins
included: each type gives its capabilities, the JSON Schema of its configuration (the console
draws the form from it) and its **secret fields**.

**Secrets of a connector are typed once.** Each secret field of a connector (`client_secret` of
SharePoint, `api_token` of Jira…) is a password input: the value is sealed in the database
([ADR 0058](adr/0058-connector-secrets-are-write-only-values.md)) and **never shown again** — the
console shows `set · rotated 3 d ago by x`, with *Replace* (type a new value) and *Clear*. An
operator who prefers a vault can instead give a **reference** (`env:NAME`, or a plugin scheme) behind
*use a reference instead*. The API: `secret_values` on create or update, `PUT|DELETE
/orgs/{org}/connectors/{name}/secrets/{field}` (the organisation's administrator) and
`PUT|DELETE /projects/{id}/connectors/{kind}/secrets/{field}` (`connector:write`). If the deployment
holds no secrets key, typing a value answers 409 and says so ([deployment](deployment.md#secrets)).
An org connector can be edited with `PATCH /orgs/{org}/connectors/{name}`.

Until a project can run, its overview opens on a **getting started** list: provisioned, a workflow
published, each required capability covered (by the project's connector, tested, or by the
platform's default), no configured connector failing its last test, and a first request — each
missing step says why and links to where it is fixed. The list disappears once every step is done.
Human groups are not checked yet.

A secret is never written in `config` — a secret field there is refused (`422`). It is named by
**reference**, field by field, and resolved when the adapter is built, in the process that uses it:

```bash
curl -X PUT $API/projects/acme:hr/connectors/tracker -d '{"type": "jira",
  "config": {"base_url": "https://acme.atlassian.net", "email": "bot@acme.test", "project_key": "RH"},
  "secret_refs": {"api_token": "env:CONNECTOR_JIRA_TOKEN"}}'
```

The core reads `env:NAME`; a plugin declares other schemes (a vault) with
`choregos_core.secrets.declarer_un_resolveur`. A reference the process cannot resolve fails the
connector test with its name — never a silent fallback to a default value.

`env:NAME` reads only the variables matching `CHOREGOS_SECRET_ENV_PATTERN` (default
`^CONNECTOR_[A-Z0-9_]+$`, chart value `global.secrets.envPattern`); the platform's own variables
(`CHOREGOS_*`, `DATABASE_*`, `PG*`, `AWS_*`, `KUBERNETES_*`, `TEMPORAL_*`) are refused whatever the
pattern. A name outside it is a `422` at write and a connector in error at use. Only the
organisation's administrator writes a reference: a project owner's `PUT` of a connector with one is
refused. **Breaking:** a reference written before this rule that is outside the pattern stops
resolving; rename the variable (or widen the pattern) and rewrite the reference.

**The organisation's connectors.** A directory, a device manager, an MCP server is declared once
by the organisation's administrator (*Administration → connectors*, `POST /orgs/{org}/connectors`),
named, and shared by its projects. It is born with the operations its type declares — a read
`allowed`, a write `approval` (a governed action, [ADR 0035](adr/0035-governed-actions-in-the-core.md)) —
and each operation carries its **policy** (`allowed`, `approval`, `forbidden`), the **project
groups** it opens to (none: every project) and a price. Only the administrator decides them
(`tools:grant`). A project sees the operations its groups open (`GET /projects/{id}/operations`)
and may only **tighten** one: `PUT /projects/{id}/operations/{connector}/{operation}` refuses an
`approval` turned into `allowed` (`422`); `DELETE` returns to the organisation's policy, which a
later hardening by the organisation always overrides.

**An MCP server** is a connector of type `mcp`: its URL, and its key by reference
(`secret_refs: {"token": "env:CONNECTOR_SUPPLIER_KEY"}`); the token is sent as a Bearer by default, or as
`Basic base64(username:token)` with `auth_scheme: basic` and a `username`
([Atlassian's Rovo MCP Server](integrations.md#atlassians-rovo-mcp-server-as-a-connector)). It declares no operation — they are
**discovered**: `POST /orgs/{org}/connectors/{name}/discover` (or *discover* in the console) runs
`initialize` and `tools/list` over Streamable HTTP — JSON or SSE answers, the session the server
gives, every page of the list — and returns the diff. **A new tool is born closed**
(`forbidden`): a server does not add a capability behind the administrator's back. A tool whose
input schema **drifts** — `order_laptop` suddenly taking a quantity — is closed again; one the
server stops offering is removed, with what projects tightened of it. A tool annotated
`readOnlyHint` is a read; any other is a write. Only the connector's key reaches the server —
never a person's token, never a run's; a key the process cannot resolve answers `502` naming it,
and the connector shows the error.

**Runs reach these servers through the platform.** An agent's version names the servers it uses
and the tools it takes from each — `mcp_servers: [{"connector": "supplier", "tools": ["track_*"]}]`
(`["*"]` for all; a server named without patterns opens nothing). A run of that agent sees, as
`supplier__track_order`, the tools that are **in its selection, `allowed`** for its project
(after the project's tightening) and open to its groups — an `approval` operation is a governed
action, not a tool. The agent calls it through `choregos-tools`; the platform checks the
arguments against the tool's schema (`400`, nothing billed), counts the call against the run's
`tool_calls_per_run` (`429`), calls the server **with the connector's key, resolved in the API**
— the run's pod holds only its run token — records the cost (`provider: mcp:<connector>`,
the operation's price) and a `tool.called` event, and passes what the server returns through the
injection guard: `warn` adds the suspicions to the answer, `block` withholds it (`451`). A tool
outside the selection, `forbidden` or awaiting approval does not exist for the run (`404`).

**The MCP catalogue** ([ADR 0065](adr/0065-catalogues-of-mcp-servers-and-skills.md)) is a list
of well-known MCP servers shipped in the image, so an administrator does not need to know a
vendor's URL by heart: `choregos_core/mcp_catalogue/catalogue.yaml`, about a hundred entries,
validated by a JSON Schema. **An entry is an offer, not a connector**: nothing is installed, no
tool is open, no network call is made by listing it. Each entry says who publishes it, whether it is
*official* (the registry namespace, the repository owner or the host belongs to the vendor, decided
from a reviewed vendor map and never set by hand), its licence, its stars with their date, how it
authenticates (`none`, `bearer`, `header:<Name>` or `oauth`) and the host the API must be able to
reach (`egress_hosts`). What a release can do with an entry is computed, not stored:
`installable` (a hosted Streamable HTTP server with a static key or none), `needs_oauth` (listed, not
installable until a connector can carry a per-user authorisation) and `needs_hosting` (a server
that runs as a local process: running a third party's code is the operator's decision, so it is
listed with what to run and nothing more).

**Installing an entry** is `POST /orgs/{org}/mcp-catalogue/{entry_id}/install` (the organisation's
administrator only; *Administration → connectors → MCP catalogue* in the console: search, category, *official only*, *installable only*; an install opens a panel for the name and the secret reference, then discovers the tools and lands on the connector). It declares an
ordinary `mcp` connector through the same door as `POST /orgs/{org}/connectors`: `{"name": "notion",
"secret_refs": {"token": "env:CONNECTOR_NOTION"}}` (the name defaults to the entry id; the
credential is a reference, never a value, and an entry that needs none refuses one). The connector
remembers `catalogue_entry: "<id>@<catalogue_version>"`, so the catalogue shows which entries are
installed and which have an update; it has **no operation** until *discover* runs, and each tool is
born closed as for any MCP connector. An entry that is `needs_oauth` or `needs_hosting` answers
`422` with the reason. `GET /orgs/{org}/mcp-catalogue` filters on `q`, `category`, `install` and
`official`, and says per entry whether the deployment declares its host reachable from the API
(`egress`, see [deployment](deployment.md#the-mcp-server-catalogue-egress-and-your-own-entries)). A
server that wants its key in another header than `Authorization` (`X-API-Key`) takes `auth_header` in
the connector's configuration.

The file is **generated, not typed**: `make catalogues` replays recorded answers of the official
MCP Registry and of GitHub over the reviewed seed in `tools/catalogues/mcp/seed.yaml`, drops
what the rules refuse (archived, deleted, tunnel or relay hosts, anonymous hosting, aggregators,
arbitrary command execution, a hosted process without an open licence) and writes the same bytes
every time; `make catalogues-check` fails when the committed file differs. To propose a server, add
a line to the seed and run `make catalogues-record` (network and `GITHUB_TOKEN`), then review the
diff — see the [runbook](runbooks/refresh-catalogues.md).

**A directory: `entra`** (Microsoft Entra ID through Graph, kind `identity`) is declared like any
connector of the organisation — tenant, application, the **administrative unit** the platform may
touch, and the application secret by reference. Its six operations come with their input schema:
`get_user` (a read, `allowed`), `create_user`, `add_to_group`, `remove_from_group`,
`disable_user`, `revoke_sessions` (writes, `approval`). Each is **idempotent**, because a governed
action replays: an account is looked up by UPN before it is created, "already a member" is a
success, removing a missing member too. An account the platform creates enters the administrative
unit; a gesture on an account outside it is refused, named, before anything is written. Graph's
`Retry-After` is honoured. With `CHOREGOS_FAKES=1`, a scriptable Graph answers instead.

**Business families: `mdm`, `shipping`, `access_control`.** A device manager (enrol a device for
its user, wipe it when it comes back, read it), a carrier (ship a parcel, book a collection — one
per reference —, track it) and badge readers (activate a badge for its holder, deactivate it, read
it) are declared like a directory, with `kind` and the key by reference (`api_key`). Reads are
`allowed`, writes `approval`. Every write is **idempotent**, and one that contradicts the state is
refused, named: a device enrolled for someone else, a reference already taken by another parcel, a
badge the readers do not know — a mistyped UID would leave the real badge opening doors. These
families have **no real type yet**: type `demo` keeps them in memory, each process its own — a
demonstration, refused in staging and prod. Where the API and the orchestrator are several
processes, give the `demo` connector a `url`: it then reaches the fakes served by **one** pod
(`demoFakes.enabled` in the chart, `python -m choregos_adapters.fakes.serveur`), which also serves a
fake Microsoft Graph for an `entra` connector whose `graph_url` and `login_url` point to it, and a
demonstration environment for a `cd: demo` connector (`/cd/mcp`): the release train of a demo
project promotes, checks and rolls back there, with no Argo CD — refused in staging and prod too. A supplier's agent needs no family: it is an MCP
server like any other, its tools discovered and born closed.

**Market data: `marketdata`.** Daily prices for stocks and ETFs, read **through the platform**: the
run's sandbox keeps its egress closed, provider keys stay on the API side (`secret_refs`, never in
the pod), and every call is a row of the cost ledger. Read-only, so both operations are `allowed`:
`get_bars` (`symbol`, `start`, `end` as `YYYY-MM-DD` — bars sorted, one per date, `adj_close` null
when the source has none) and `get_instrument` (`symbol` — name, `stock`/`etf`/`unknown`, currency,
exchange). A run sees them as `<connector>__get_bars` and `<connector>__get_instrument`. A symbol
must match `^[A-Za-z0-9.^=_-]{1,20}$` and can never change the URL; an unknown symbol or a quota
is a named error (`422`).

| Type | Source | Config | Secret | Mind |
| --- | --- | --- | --- | --- |
| `stooq` | daily CSV | `suffix_map` (ticker suffix to Stooq market: `""` is `.us`, `.L` is `.uk`, `.DE`, `.HK`, `.T`, `.WA`, `.PA` are mapped) | `api_key` (optional, appended as `&apikey=` when Stooq asks for it) | not dividend-adjusted; only `.us` is a known quantity, the other markets are unverified, hence the map; on 2026-10-14 Stooq answered scripts with a JavaScript browser check, reported as a source failure |
| `yahoo` | unofficial chart endpoint | — | — | **personal use only, may break** without notice; has `adj_close` |
| `alphavantage` | `TIME_SERIES_DAILY` | — | `api_key` | free tier about 25 requests a day, unadjusted, recent history only |
| `composite` | the three above | `sources` (types, in order), `source_configs`, `cross_check` (default true), `tolerance` (default 0.02) | `stooq_api_key`, `alphavantage_api_key` | falls back on an error or an empty answer; with `cross_check` the next source that answers is compared on common closes: `cross_check: {source, common_days, max_rel_diff, diverging_days}` — it reports a divergence, it does not pick a winner |

The composite lists provider **types**, not other connectors (an adapter is built from one
connector's configuration and secrets), hence the flat `<type>_api_key` secret fields. With
`CHOREGOS_FAKES=1` a `fake` serves deterministic synthetic bars (a random walk seeded by the symbol,
business days only). Every tool call also records `request_sha256` and `response_sha256` (SHA-256 of
the canonical JSON) in its `tool.called` event, so a result can later be matched to what the run was
served.

`memory: lexical` keeps the project's memory in Choregos's own database — no extra service,
and a lexical, not semantic, search: there is no `vector` extension behind it. It was called
`pgvector` until 2026-09-26, which promised something the code does not do; that name still
works as a deprecated alias.
a lexical ranking instead of embeddings, the same governed writes. The connector type decides
for both the API and the orchestrator; on PostgreSQL the memory is read under the project's
organization, like everything else under row-level security.

`gateway: direct` lets agents use credentials they bring themselves. It is the honest choice
for a bench, and it says what it costs: **no spend is measured and no cap applies** — only
the turn and minute budgets still hold.

## 3. Write the workflow

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata: { name: default-simple, version: 1 }
actors:
  dev:      { type: agent, role: implement, model: "profile:standard", max_turns: 40, max_minutes: 30 }
  reviewer: { type: agent, role: verify,    model: "profile:standard", max_turns: 20, max_minutes: 15 }
  owner:    { type: human, group: maintainers, sla_hours: 24 }
states:
  inbox:       { display: Inbox, kind: wait }
  in_progress: { display: In progress }
  verifying:   { display: Verifying }
  done:        { display: Done, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
transitions:
  - id: t-implement
    from: inbox
    to: in_progress
    by: dev
    gates: [evidence_present]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-verify
    from: in_progress
    to: verifying
    by: reviewer
    gates: [evidence_present]
    on_fail: { to: in_progress, max_attempts: 2, escalate_to: needs_human }
  - id: t-accept
    from: verifying
    to: done
    by: owner
    timeout_hours: 48
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
```

Validate before you commit it:

```bash
choregos workflow validate my-workflow.yaml   # add --remote to validate server-side
```

The validator refuses more than syntax: an unreachable state, a retry that can loop forever,
an unknown gate, or **a gate that would have nothing to check** — `outputs_present` on a
transition that declares no `outputs:`. If you mean it, say so: `params: { allow_empty: true }`.

**What the platform does is written, not named.** A system transition says what the platform does
when it takes it — `does: open_pr` opens the ticket's pull request, `does: merge_pr` merges it — and
a production state says so — `production: true`, reachable only through a release train:

```yaml
states:
  review:  { display: In review, kind: wait }
  live:    { display: In production, terminal: true, production: true }
transitions:
  - { id: t-open, from: verifying, to: review, by: ci, does: open_pr }
```

Before this, the *name* of the target state carried the effect (`pr_*` opened, `merged*` merged,
`deployed_prod*` was production): renaming a state silently changed what the workflow did, or
lifted the production lock. Those names are still read, so a published workflow keeps its
behaviour, but the validator warns (`workflow.implicit_effect`), and a rename from the console
first writes the effect down.

Two validation codes were renamed to English (ADR 0046): `workflow.implicit_effect` (formerly
`workflow.effet_implicite`) and `gate.nothing_to_check` (formerly `gate.sans_matiere`). For one
release, an issue that carries one of them also carries its former spelling in `deprecated_code`;
match on `code`.

The console's **workflows** tab (`/p/<slug>/workflows/<name>`) shows each workflow four ways:
the process (each transition in plain words), the map (one lane per kind of actor), the YAML
editor (it validates as you type) and the version history. The map is keyboard-navigable: Tab
reaches the states in reading order, ← and → follow transitions, ↑ and ↓ move between states,
Home and End jump to the ends, Enter opens the state for editing. The state under the cursor is
described below the map, with its outgoing transitions, actors and gates — that sentence is what
a screen reader announces.

### Several workflows in one project

A project runs as many workflows as it needs — onboarding and offboarding in an HR project, a
delivery flow and a maintenance flow in a platform team ([ADR 0031](adr/0031-several-workflows-per-project.md)):

```bash
curl -X PUT  $API/projects/acme:hr/workflows/onboarding  -d '{"yaml": "..."}'   # publishes the next version
curl -X PUT  $API/projects/acme:hr/workflows/leaver -d '{"yaml": "...", "create_only": true}'  # creates; a taken name → 409
curl -X PUT  $API/projects/acme:hr/workflow-routing -d '{"default": "onboarding",
  "rules": [{"when": {"labels_any": ["leaver"]}, "workflow": "offboarding"}]}'
curl         $API/projects/acme:hr/workflows                                    # one active version per name
```

- One version is active per name; every publication adds a version, none is overwritten, and
  restoring an old version publishes it as the next one.
- A work item is bound at birth — an explicit `workflow`, else the first routing rule that
  matches its labels or its tracker type, else the default — and **pinned to that version**:
  publishing a new version never moves a running item.
- `PUT /projects/{id}/workflow` remains, as the alias of the default workflow.
- A stack template ships them to every project born from it, in its `manifest.yaml`:

  ```yaml
  defaults:
    workflows: [workflows/onboarding.yaml, workflows/offboarding.yaml]   # or template:<name>@<v>
    default_workflow: onboarding
    routing:
      - { when: { labels_any: [leaver] }, workflow: offboarding }
    policy: preset:team
  ```

  A path is read from the template's folder and may not leave it; a template published through
  `POST /templates` has no folder, so it ships core workflows only (`template:<name>@<v>`). A
  template that names a workflow it does not ship, or ships an invalid one, creates no project
  (`422`). The singular `workflow:` of older manifests still works.
- The default and a routing target cannot be deactivated; a deactivated workflow takes no new
  item, and its pinned items finish on their version.
- In the console, the project's **workflows** page does the same: **make default** and
  **deactivate** on each workflow (both confirmed; deactivate is offered only when the workflow is
  neither the default nor a routing target, and says why otherwise), and a **routing** card that
  adds, orders and removes rules as a draft, saved in one gesture. A new workflow cannot take the
  name of an existing one: publishing it would replace that workflow's active version.
- **Moving a running item to another version** is explicit:

  ```bash
  curl -X POST $API/work-items/ACME-12/actions \
    -d '{"action": "migrate", "workflow_def_id": "<id>", "state_mapping": {"triage": "request"}}'
  ```

  The API checks that the version belongs to the project and that the item's current state exists
  in it, or is mapped — `422` otherwise, and nothing is sent. An item waiting on a human decision
  or running an agent answers `409`: pause it, let the run finish, then migrate. A parked or
  paused item moves at once, its pin follows the new version, and its timeline shows
  `workitem.migrated`; if its state changed in between, the migration is refused
  (`workitem.migration_refused`) and the item carries on, on its own version.

### Bringing a project to a newer template version

A project receives its template's workflows once, when it is born; a newer template version does
not reach it by itself. To adopt one (for instance `dev-complex` version 2 of
`github-software-delivery` 1.1.0, which adds a documentation step), publish the template's YAML as
the workflow's next version, from the console's YAML tab or the API with the version you are
replacing as `base_version`; the catalogue agents the workflow names install themselves
(ADR 0040). The running Choregos must already know the gates the YAML uses, or validation refuses
it.

```bash
curl -X PUT "$API/projects/acme:dev/workflows/dev-complex" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --rawfile y dev-complex.yaml '{yaml: $y, base_version: 1}')"
```

Running items stay pinned to their version until they are migrated (see above); every state of
the old version exists in the new one, so no `state_mapping` is needed.

### Editing a workflow without rewriting it

The console's map and process view change a workflow through typed operations, grafted into the
YAML text: everything else — comments, quoting, flow or block style — stays byte for byte
([ADR 0031](adr/0031-several-workflows-per-project.md)). The same call is open to any client:

```bash
curl -X POST $API/workflows/edit -d '{"yaml": "...", "operations": [
  {"op": "add_gate", "transition": "t-implement", "gate": "ci_green"},
  {"op": "rename_state", "from": "triage", "to": "intake"}]}'
```

| Operations | On |
| :-- | :-- |
| `add_state`, `remove_state`, `rename_state`, `set_state` | states — a rename follows every reference |
| `add_transition`, `remove_transition`, `set_transition` | transitions, by `id` |
| `add_gate`, `remove_gate` | a transition's guarantees |
| `add_actor`, `remove_actor`, `set_actor` | actors |

The answer carries the edited `yaml`, its unified `diff`, the validation and the graph, and the
`inverse` operations, which give back the original bytes. Nothing is saved: the console saves
with `PUT /projects/{id}/workflows/{name}` and the `base_version` it read. A state or an actor
still named elsewhere cannot be removed (`422`); renaming a state whose name carried an effect
(`pr_*`, `merged*`, `deployed_prod*`, in a workflow written before effects were explicit) first
writes that effect down — `does` on the system transitions that reach it, `production: true` on the
state — so the rename changes nothing the workflow does, and `notices` says so.

In the console, a click on a state or a transition of the map — or **edit** on a step of the
process view — opens its panel. A state has its label, its name, a new transition (to a state
that exists, or to a new one) and its removal; a transition has who moves it, its guarantees, its
time limit and its removal. Each change is one operation. The map and the process view redraw
from the same draft, which shows its last diff and undoes change by change (undo replays the
inverse). Nothing is saved before **publish**, which sends the text with the version that was
read: if someone published meanwhile, the console says so (`409`) instead of overwriting.
**add a step**, at the end of the process view and beside the map, adds a transition from any
state to an existing or a new one without choosing a state first. The draft survives a detour
through another page of the console (it is kept for the browser tab), and **discard** asks first.

### What each step shows

The process view describes every step from the workflow's YAML (`process` in the validation
answer and on the ticket's journey):

- **Who**: for an agent step, the registry agent it names (`agent`, `slug` or `slug@N`, which is
  not the actor's key), its role and model; for a person, the group and the SLA; for a job, the
  command and the image.
- **Reads**: the `inputs` of the transition. The map's artifact flow says where each comes from:
  another step, a person's rejection reason, a work item field, or nothing (`missing`).
- **Produces**: the `outputs`, typed when `output_types` declares a file type, and
  `writes_branch` for an agent or job step whose transition carries a gate that reads the diff
  (`scope_respected`, `no_secrets`, `diff_size_max`, `markdown_sections`, `adr_number_free`): it
  changes the work item's branch even though it lists no output.

In the console, the **Process** tab shows this as a card per step: the agent's name (a link to
`/agents/<slug>`) with a chip, "v1 · pinned", "v3 · latest", "not installed: installs when this
workflow is published", "unknown agent" or "unavailable: …"; a person's group and SLA; a job's
command; **Reads** with the source of each document ("from *Tested* (`t-test`)", "a person's
rejection reason", "a work item field", or "nothing produces it" as a warning); **Produces**, with
"changes on the work item's branch" for a step that writes code. On the **Map**, each box carries
one line, "→ spec_markdown, allowed_paths" or "→ branch", and an **Artifacts** table under the
diagram lists each document with its type, its producers and its readers; hovering or focusing a
row outlines those boxes. Choosing a box shows the same card above its editor.

`graph.artifacts` is the artifact flow: one entry per document with `produced_by`, `read_by`,
`origin`, `context` (given to every agent stage) and `in_pull_request`.

Which agent version a run would load for those references, through the runtime's own resolver
(an explicit `@N`, else the project's pin, else the latest version), is read, without changing
anything, with:

```bash
curl "$API/projects/$PROJECT/agent-resolution?ref=developer&ref=spec-writer@2"
# [{"ref": "developer", "slug": "developer", "status": "resolved", "version": 1, "pinned": true, ...},
#  {"ref": "spec-writer@2", "status": "not_installed", "in_catalogue": true, ...}]
```

`status` is `resolved`, `not_installed` (the organisation has no such agent; `in_catalogue` says
whether publishing the workflow installs it) or `unavailable` (it exists but a run would not
start: draft, retired, expired, disabled; `reason` has the runtime's own words). One to 64
`ref` parameters.

### Workflows outside software

The same engine carries work that has no repository. Name your own roles, bring your own
playbooks, and use the gates that do not speak of code:

```yaml
actors:
  sourcer: { type: agent, role: sourcing, model: "profile:standard" }
transitions:
  - id: t-sourcing
    from: request
    to: sourcing
    by: sourcer
    outputs: [profiles]
    gates:
      - name: outputs_present
      - name: evidence_facts
        params: { keys: [profiles_kept], min: { profiles_kept: 1 } }
```

`evidence_facts` reads what the agent **counted**, not what it narrated, and `min` refuses a
polite zero: finding nobody is said by failing the step. `demo/workflows/staffing.yaml` is a
complete example that runs on the same deployment as the software one, with no code change.

**Every declared output is required** (ADR 0049): a step that reports `done` without one of its
`outputs:` fails and is retried or escalated like any failure, with or without an
`outputs_present` gate. An output is a value of the result (markdown or JSON) unless
`output_types` says it is a file:

```yaml
    outputs: [summary_markdown, budget, deck]
    output_types: { budget: xlsx, deck: pptx }
```

A typed output is a file the agent writes in `.choregos/outputs/` (`budget.xlsx`, `deck.pptx`); a
value written in `outputs` under the same name does not stand for it. The types are `markdown`,
`json`, `mermaid`, `sarif`, `text`, `csv`, `html`, `pdf`, `docx`, `xlsx`, `pptx`, `png`, `jpeg`,
`gif`, `webp`, `svg`, `mp4`, `webm` and `file`; the validator refuses an unknown type, a type for an
output `outputs:` does not declare, and a name that cannot be a file name.

Every output is kept as an **artifact** of the ticket, revision by revision:
`GET /work-items/{id}/artifacts` lists them (type, media type, size, sha256, the run and agent that
produced each), `GET /work-items/{id}/artifacts/{name}` (or `{name}@{version}`) describes one, and
`…/content` serves its bytes after checking the digest again — altered content is refused (409).
On a work item's page, the **artifacts** card has one tab per output: its revisions, the run and
agent that produced each one and the runs that read it (the hand-off receipts), a viewer — markdown
rendered without HTML, a SARIF report as a table, an image, a PDF in the browser's own viewer, a
video in a player — and a download. Office files (docx, xlsx, pptx) are downloaded, never rendered
in the browser. A revision the API
refuses to serve is said, not shown.
A step's declared `inputs:` reach its agent as files too: the runner writes each one in
`.choregos/inputs/<name>.<ext>` (`deck.pptx`, `profiles.json`, `spec_markdown.md`) after checking
its digest, and lists them in `.choregos/task.md`. A large report no longer has to fit in the
prompt; an input whose bytes do not match stops the run.

**Binary deliverables.** An agent that produces a slide deck, a spreadsheet, a document, an image
or a video writes it in `.choregos/outputs/`. The runner hands each file to the platform, which
checks its sha256, stores it in the object store and records it as a typed artifact; the runner
lists what was stored in the result's `artifacts.files` — an agent cannot vouch for its own files.
A declared file that is missing goes back to the agent, then fails the step. Undeclared files are
kept too, under their own file name. Generating them is not the platform's job: office files come
from skills run in the sandbox, images and video from models behind the gateway (their cost is in
the ledger like any model call) or from MCP connectors. Files are limited to
`CHOREGOS_ARTIFACT_MAX_BYTES` (200 MiB by default) and are never committed.

**Deterministic steps** (ADR 0056). A computation — a backtest, a data snapshot, a conversion — is
code, not judgement: give it to a `job`, not to an agent.

```yaml
actors:
  engine: { type: job, role: backtest, command: [choregos-quant, backtest, --out, out], image: quant, max_minutes: 20 }
```

`command` is an argv, never a shell line. `image` names an image the operator mapped in
`CHOREGOS_JOB_IMAGES` (`{"quant": "registry/…@sha256:…"}`), built from the runner image; without it
the job runs in the runner image, and an unknown name stops the step. A job gets no model and no
gateway key. The runner writes the work item's fields in `.choregos/fields.json`, runs the command
with `CHOREGOS_WORKSPACE`, `CHOREGOS_RUN_ID`, `CHOREGOS_API_URL` and the run's own token
`CHOREGOS_RUN_TOKEN` (to call a connector through the internal API), and reads back
`.choregos/job.json` = `{"summary": …, "facts": {…}, "outputs": {…}}` and the files in
`.choregos/outputs/`. A non-zero exit fails the step; the facts are **measured**, so
`evidence_facts` gates on what the code computed, not on what an agent said. Retries, `on_fail` and
the `*agent` wildcard treat a job like an agent step. A job reaches the outside only through the
connector operations its actor names, `tools: [{connector: marketdata, tools: [get_bars]}]`, called
through the internal API with that token; a job that names none sees no tool.

The `joiners-leavers` template goes further, with no agent writing anything itself: an agent
prepares the access plan with its skills, a person from HR validates it, and every write is a
**governed action** at a date — the account at D-10, the sensitive group on a re-authenticated
approval, the laptop ordered from the supplier's agent at D-7, the badge a **task** with its proof,
the check at D+1; at departure, D0 cuts the account, its sessions and the badge in one gesture.
See [§4 bis](#4-bis-governed-actions).

## 3 bis. Give agents tools they cannot misuse

Agents get their platform tools over MCP on localhost — `report_finding`, `ask_human`,
`request_scope_change` — and, if the deployment declares one, the **tool catalogue**: third
party APIs and MCP servers the platform calls *for* them.

What a project has to say, in its own configuration:

```yaml
tools: [verifier_adresse, rechercher_entreprise]   # what it uses; [] means none
groups: [rh]                                      # what it is entitled to
```

Those two lines are not redundant. `tools` is **what this project uses** and its team can
edit it. `groups` is **what the deployment opens to it**, and it is matched against the
`groups` declared on each catalogue tool. A project that lists a tool reserved to a group it
does not belong to simply does not get it — the listing is not the control.

Three properties worth knowing before you wire an external provider:

| | |
| :-- | :-- |
| The agent never holds a provider credential | The platform makes the call; the agent's egress stays closed |
| Your run token never reaches the provider | It authenticates the agent *to Choregos*, nowhere else |
| The agent chooses neither URL nor method | It names a catalogue tool; the rest is written in the catalogue |

An unauthorised tool answers **404, not 403**: an agent has no business discovering the
deployment's catalogue by guessing names.

**A write is a governed action, even when the policy allows it.** An agent that calls an
organisation connector's *read* operation gets a direct answer. A *write* — create an account,
order a laptop — becomes a governed action the policy approves at once: recorded under its key,
done once, compensated if a later step fails, played by Temporal. The agent still gets what the
server answered (the call waits up to `courtier_attente_ecriture_s`, 30 s, then answers `202` with
the action's id), and calling it again with the same arguments returns the same action instead of
writing twice. A write set to `approval` waits for a person instead ([ADR 0034](adr/0034-connectors-by-capability.md) §7).

Spend is bounded per run (`budgets.tool_calls_per_run`) and every call lands in the cost
ledger under `kind: tool` — visible on the project overview and in the run's access record.

**Announcing a tool is not using it.** On the 2026-09-24 bench the sourcing playbook said
"verify the location with `verifier_adresse` before anything else", the agent recorded
`lieu_verifie: true`, and the ledger held ten catalogue listings and zero calls. When a
step *must* use a tool, say so with a gate, which reads what the platform counted:

```yaml
gates:
  - { name: tool_called, params: { tools: [verifier_adresse] } }
```

Agents also get `validate_result`, which checks their `.choregos/result.json` against the
contract *before* they finish — the same validator the runner applies afterwards.

## 3 ter. Register your agents

An agent is an object of the organisation ([ADR 0033](adr/0033-agent-registry-and-skill-library.md)):
`internal` when the platform runs it, `external` when it is a client of the MCP door. What it *is*
— instructions, model, limits, budget, skills, MCP servers and the tools it may call — lives in
**versions that never change**: a change publishes the next one.

```bash
curl -X POST $API/orgs/acme/agents -d '{"slug": "onboarding-coordinator",
  "display_name": "Onboarding coordinator",
  "spec": {"instructions": "Prepare the access plan of a newcomer…",
           "budget": {"run_usd": 2, "daily_usd": 20},
           "mcp_servers": [{"connector": "entra", "tools": ["entra_read_*"]}]}}'
curl -X POST $API/orgs/acme/agents/onboarding-coordinator/versions -d '{...}'   # v2; v1 is kept as is
curl -X PUT  $API/projects/acme:hr/agents/onboarding-coordinator \
  -d '{"version": 2, "overrides": {"budget": {"run_usd": 1}}}'
```

In the console, **Agents → New agent** opens a full page (`/agents/new`), in two steps:

1. **Describe.** Two fields: the *name* and *what it should do* (ten characters at least; it becomes the
   agent's description). **Draft it for me** asks a model for a first version (see below) and goes on
   with the form pre-filled: instructions, suggested skills, knowledge bases and tools (their sections
   open), the sample of **Try it**, and, above the form, what the platform changed in the draft. It
   saves nothing. If the draft fails (too many drafts in a minute, gateway down), the page says so.
   **Start from a blank form** goes on empty; **Or add one from the catalogue** goes back to the
   catalogue's drawer.
2. **Configure.** The *basics* are always open: name, slug (derived from the name, editable until the
   agent exists, then fixed; `new` is refused because `/agents/new` is a page), description and
   instructions. The seven other sections are folded and each says its value in one line: model and
   runtime, limits and budget, skills, tools (MCP servers), knowledge, memory, eval suite. A section
   opens by itself when it holds something other than the default. **Create and activate** registers
   an active agent; **Save as draft** registers it as a `draft` (nothing pins or runs it until it is
   activated, see S32-04).

   From 1280 px, a panel stays beside the form with two tabs: **Try it** (a real, capped trial run,
   see below) and **What it costs and allows** (the model's price, the operations a tool pattern
   reaches and their policy, and the projects a knowledge base is served to: a run elsewhere stops
   before it starts). Below 1280 px the panel falls under the form.

**New version**, on an agent's page (Definition tab), is the same form without the first step.

*Why not a clone of Microsoft's Agent Builder or Copilot Studio?* We took what fits and left the rest.
Kept: a short first step in which the author says what the agent is for, one form for the details with
the advanced fields folded, a test pane beside it, and draft-then-publish. Not kept: a conversational
"describe" turn (one round, describe then edit the form, is enough), starter prompts (a Choregos agent
is a stage worker invoked on a work item, not a chat assistant, so its equivalent is the eval suite
and the trial's sample input), topics, triggers and channels (workflows are the triggers, and the AI
clients page is the channel), and a visual canvas (the definition stays data, YAML or JSON, with a
form on top; OpenAI is retiring its visual Agent Builder on 2026-11-30). Drafting the instructions
from the description with a model is a later step; until then, step 1 starts a blank form.

An agent carries **skills** — a folder with a `SKILL.md` whose header gives its `name` and
`description` — from the organisation's library:

```bash
curl -X POST $API/orgs/acme/skills/import -H 'Content-Type: application/zip' --data-binary @onboarding.zip
```

### Draft an agent from a description

Writing instructions is the hard part, so the platform can propose a first version
(`POST /orgs/acme/agent-drafts`, `agent:manage`):

```bash
curl -X POST $API/orgs/acme/agent-drafts -d '{"display_name": "Release notes writer",
  "description": "Writes release notes from the pull requests merged since the last tag."}'
```

The answer is an `AgentDraft`: a slug, a `spec` (numbered instructions, suggested skills, knowledge
bases, connectors and tool patterns, limits, an eval case or two), a `sample` work item for **Try
it**, `notes` and the `model` that wrote it. **A draft is a proposal and is never saved**: a person
reads it, edits it and creates the agent (`POST /orgs/acme/agents`) themselves. One model call on the
gateway, capped at 2,500 output tokens and 60 seconds, with the instructions of the catalogue's
`agent-drafter` — or the organisation's own version of it, once installed and edited, as for the
knowledge distiller. The model sees the description (marked as data) and only the names and one-line
descriptions of the skills, knowledge bases and connectors the organisation has; anything it suggests
beyond them is dropped and listed in `notes`, limits are clamped, and instructions that use a template
variable outside the sandbox's list are emptied with a note. Ten drafts a minute per person (429 with
`Retry-After`); a gateway failure is a 502. The slug `new` is reserved (422): `/agents/new` is the
console's page. The drafting model call appears in the gateway's logs under `agent-drafter`, not in
Choregos' spend ledger.

A skill **declares no permission**: an `allowed-tools` field is refused, because what an agent may
call is decided by its version and its project, never by a file it reads. An archive that leaves
its folder (`../`), holds a symbolic link, more than 256 files or 5 MiB, or a file that is not
UTF-8 text is refused. Versions never change; a skill says which agents carry it.

Every version passes a **supply-chain gate** ([ADR 0050](adr/0050-skill-supply-chain-gate.md)): a
risk tier — `low` (a `SKILL.md` alone), `medium` (references, assets), `high` (a script, an
executable file, an outbound URL, a credential-like string, an MCP reference) —, a static scan
(prompt-injection phrases, hidden or bidirectional Unicode, encoded blobs, `curl … | sh`) whose
findings name their file and line, and a licence check (a missing or non-open licence is flagged,
not refused). A **high-risk version waits for a second person**: until an administrator who did not
import it approves it, no agent may pin it and no run receives it:

```bash
curl -X POST $API/orgs/acme/skills/fill-forms/versions/1/approve   # 403 for its importer
```

A skill can also come from a **git repository, at a pinned commit**
([ADR 0050](adr/0050-skill-supply-chain-gate.md)). The preview resolves a branch or a tag to its
commit SHA and lists the skill folders it finds; the import then names that SHA — a branch or a
tag is refused, since it can change under you — and the skills you picked:

```bash
curl -X POST $API/orgs/acme/skills/import-git/preview \
  -d '{"repo_url": "https://github.com/anthropics/skills", "ref": "main", "path": "skills"}'
curl -X POST $API/orgs/acme/skills/import-git \
  -d '{"repo_url": "https://github.com/anthropics/skills", "sha": "<40 hex>", "path": "skills",
       "skills": ["mcp-builder"]}'
curl $API/orgs/acme/skills/mcp-builder/update            # a proposal: the new SHA and the files changed
```

Only `https://github.com/…` and `https://gitlab.com/…` repositories are fetched, as a tarball,
public ones for now. Each version records its repository, SHA, folder and licence. A project
owner (`skill:import`) imports new skills from the organisation's **allowlist**
(`GET|PUT /orgs/{org}/skill-sources`; by default `github.com/anthropics/skills` and the GitHub
owners of your connected repositories); another source, or the next version of an existing skill,
needs an administrator. Checking for an update never applies it: importing the proposed SHA does.

**The skills catalogue** ([ADR 0065](adr/0065-catalogues-of-mcp-servers-and-skills.md)) offers the
most used skills of public repositories, so `/skills` is not empty on day one:
`choregos_core/skill_catalogue/catalogue.yaml`, generated from a reviewed seed at **pinned commits**.
An entry is an offer: nothing is installed by default and nothing follows upstream. Each says its
publisher, repository, pinned SHA and folder, **licence** (`permissive` or `copyleft`), size, the
supply-chain gate's **risk** at generation time, and its **delivery**:

- `bundled`: the skill's files are vendored in the image under `bundled/<id>/`, with their licence
  text and a `PROVENANCE.json` of sha256s, so installing needs no network. Only permissive licences
  (MIT, Apache-2.0, BSD, ISC, CC0, CC-BY-4.0) and a low or medium risk are bundled; the image's
  [third-party notices](https://github.com/VargaFoundation/choregos/blob/main/THIRD_PARTY_NOTICES.md)
  list what it redistributes.
- `git`: everything else (copyleft licences such as MPL-2.0, anything high risk — an outbound URL is
  enough — and large skills): the entry holds the pin, and the install fetches that commit.

What is **not** listed, on purpose: Anthropic's `docx`, `pdf`, `pptx` and `xlsx` (governed by an
agreement with Anthropic, not an open licence), skills without a licence file, skills that declare
`allowed-tools`, skills that carry binary files, repositories whose owner is not on the publisher list
(`tools/catalogues/skills/publishers.yaml`: a vendor, or a maintainer with at least 1 000 stars and a
repository that is not archived), and a few that only make sense inside one coding assistant. A
person with the right to use a skill that is not listed can still import it by hand, above, where
the gate flags its licence and a person decides.

The file is **generated, not typed**: `make catalogues` replays the recorded commits over the seed in
`tools/catalogues/skills/seed.yaml`, runs the platform's own reader, licence detection, gate and
digest on each skill, and writes the catalogue, the bundled tree and `THIRD_PARTY_NOTICES.md`
byte for byte; `make catalogues-check` fails when one differs. A line asking for `delivery: bundled`
is a request: a skill that is not eligible is delivered by `git` and the generator says so.

**Installing a catalogue skill** is `POST /orgs/{org}/skill-catalogue/{entry_id}/install` (*Skills →
Catalogue* in the console), and `GET /orgs/{org}/skill-catalogue` shows what each entry means in your
organisation: `available`, `installed`, `update` (the catalogue pins a newer version), `pending_review`
(installed, waiting for a second administrator) or `blocked` with the reason (a skill of that name that
does not come from the catalogue, or a permission you lack). An install publishes an ordinary skill
version through the same door as an import, so **the gate runs again**: the `risk` the catalogue shows is
what it said when the catalogue was generated, and a test asserts it equals what the install gets for
every entry. A `bundled` entry is read from the image and checked against its sha256 provenance (a
changed file is refused with `502`); a `git` entry is fetched at its pinned commit from
`codeload.github.com` and must hash to the digest the catalogue carries. A `bundled` version is approved
by `platform` like the agent catalogue's skills, and never when the gate calls it high risk; a `git`
version is an ordinary import: a high-risk one waits for a second administrator, so an agent can neither
pin nor carry it until then. An administrator installs any entry; a project owner (`skill:import`) a
**new** skill from a repository in the organisation's allowlist
(`GET|PUT /orgs/{org}/skill-sources`, [ADR 0050](adr/0050-skill-supply-chain-gate.md) unchanged). A skill
of the same name that was not installed from the entry is **left untouched** (`409`), and an entry
already installed at this version answers `409`. Nothing follows upstream by itself: an `update` is a
newer catalogue release and a person clicking.

A workflow actor **names** an agent — `{type: agent, role: implement, agent: onboarding-coordinator}`,
or `…@2` for a given version. The run then takes the project's pinned version (else the latest),
overrides applied: its model, its limits and its budget, which only tighten the actor's own. Its
instructions **replace the playbook**: they are a Jinja template rendered in a **sandbox**
(`{{ ''.__class__ }}` is refused, at publication already), with the playbook's variables
(`ticket`, `spec`, `inputs`…), and the platform appends a frame the author cannot remove — the output
contract and the invariants. The run records the agent and its version; a revoked, suspended or
expired agent does not start, and neither does one that has spent its `daily_usd` since midnight
(UTC) — models **and** tool calls. `GET /orgs/{org}/agents/{slug}/metrics?days=30` gives its runs,
their success rate, their cost by kind and by project, and what it spent today.

A version also says **whose memory** its runs use — `"memory": "project"` (the default: the
project's shared memory, as before), `"agent"` (its own memory within the project: what it learns
is not mixed with what the project knows, and no other agent reads it) or `"none"` (nothing read,
nothing written). The run's `StageInput` carries it (`memory_scope`); the context pack, the lessons
and the proposals of the run follow it, and the memory MCP server — which serves the project's
memory — is only handed to a run whose scope is the project.

A project **pins** a version and may only **tighten** it — a lower budget or limit, fewer tools;
an override that widens is refused (`422`). A revocation is final.

### Who owns an agent, who may change it

An agent has a **scope** ([ADR 0061](adr/0061-agent-ownership-scopes-and-forks.md)):

| scope | what it is | changed by |
| :-- | :-- | :-- |
| `platform` | a **common** agent, installed from the built-in catalogue | an `org_admin` only |
| `org` | an agent shared by every project, written by the organisation | an `org_admin` |
| `project` | one project's agent; it runs only in that project | that project's `project_owner`, or an `org_admin` |
| `personal` | someone's own agent: drafts, experiments | its owner, or an `org_admin` |

The server decides — the console only shows its answer. Each agent carries `can`
(`publish`, `manage`, `pin`, `fork_scopes`) and, when something is refused, `cannot_reason`, one
sentence saying why and what to do instead ("`developer` is a common agent: only an org_admin changes
it; fork it into your project or your own agents to extend it"). The matrix, by who may:

| action | common | organisation | project P | personal (owner U) |
| :-- | :-- | :-- | :-- | :-- |
| see | any member | any member | members of P, org_admin | U, org_admin |
| create | org_admin (catalogue) | org_admin | P's project_owner, org_admin | any developer and above (becomes U) |
| publish a version, edit, lifecycle | org_admin | org_admin | P's project_owner, org_admin | U, org_admin |
| suspend, revoke, transfer, clients | org_admin | org_admin | P's project_owner, org_admin | U, org_admin |
| pin on project Q | Q's project_owner | Q's project_owner | only Q = P | refused (`422`): promote or fork first |
| trial run | `item:control` on the trial project | same | trial project must be P | U only |
| fork into a scope | whoever sees it and may create in that scope | same | same | same |

An agent you may not see is a **404**, not a 403: a personal agent of someone else does not exist for
you. `GET /orgs/{org}/agents` returns only what you may see, and takes `?scope=` and `?project=`.

```bash
# a developer's own agent, then a project owner's
curl -X POST $API/orgs/acme/agents -d '{"slug": "my-helper", "display_name": "Mine", "scope": "personal", "spec": {...}}'
curl -X POST $API/orgs/acme/agents -d '{"slug": "hr-helper", "display_name": "HR", "scope": "project", "project": "hr", "spec": {...}}'
```

**A personal agent never runs in a project's workflow, and a project's agent runs only in its
project**: a process a whole team runs must not depend on one person's agent. Pinning one (`422`),
publishing a workflow that names one (`422`, naming the agent, its scope and the fix) and starting a
run with one (the stage stops, without retry) are all refused. A personal agent runs in **trials**
(`POST /orgs/{org}/agent-trials`, which needs `item:control` on the trial project and the right to
create a personal agent).

**Fork** a common agent to extend it:

```bash
curl -X POST $API/orgs/acme/agents/developer/fork \
  -d '{"slug": "hr-developer", "scope": "project", "project": "hr", "version": 3}'
```

The fork's version 1 is a byte-for-byte copy of the source's version 3 (the latest when `version` is
left out — same spec, same checksum). It records `forked_from` and inherits nothing else: no pin, no
client, no metrics. The source is never touched. The fork reports its upstream: `forked_from` says
`behind: true` when the source published a newer version — nothing re-bases by itself; publishing the
fork's next version from the upstream's is your decision. A developer forks into `personal`, a project
owner into their project, an `org_admin` anywhere; nothing is forked into the common agents.

**Promote** an agent to hand it over (`POST /orgs/{org}/agents/{slug}/scope`):

```bash
curl -X POST $API/orgs/acme/agents/my-helper/scope -d '{"scope": "project", "project": "hr"}'
```

A personal agent goes to a project (its owner **and** that project's owner, or an `org_admin`) or to
the organisation (`org_admin`); a project's agent to the organisation (`org_admin`); an organisation
agent into one project (`org_admin`, refused while another project pins or names it). A common agent
never moves. Existing agents were migrated by ADR 0061: those installed from the catalogue are common,
every other one is an organisation agent — nothing changed hands and no pin moved.

In the console, `/agents` carries a scope badge per agent (Common, Organisation, Project · name,
Personal) and filter chips by scope. An agent's page disables its edit, publish, lifecycle and
suspend controls when the API says `can.publish` or `can.manage` is false, and prints `cannot_reason`
in the page itself (never only in a tooltip). **Fork** opens a dialog limited to `can.fork_scopes`
(new slug, project, source version) and lands on the fork; a fork that is behind its source shows a
banner, and whoever holds `can.manage` on a personal or project agent finds **Change scope** on its
overview. A project's agents page lists the agents that project owns first, and its "Pin an agent"
list leaves out personal agents and other projects' agents. `/agents/new` asks for a scope among those
the person's memberships can create; the API stays the judge.

The permissions behind it: `agent:manage` (org_admin: organisation and common agents),
`agent:project_manage` (project_owner: the agents of that project) and `agent:create_personal`
(developer, release captain, project owner: a personal agent).

An agent also has a **lifecycle**, apart from that operational status: `draft` (no project pins
it, no run starts with it) → `active` → `deprecated` (it still runs; a pin of it answers with a
warning) → `retired`, which is final.
`POST /orgs/{org}/agents/{slug}/lifecycle {"to": "retired"}` executes the retirement checklist —
its credentials are revoked, its project pins removed, new runs refused — and is refused (`409`)
while one of its runs is in flight, unless `"force": true`; the audit log records each step.
An agent with no run for 60 days says so (`unused`, `last_run_at`), and
`POST …/agents/{slug}/owner {"email": …}` hands it to another member, credentials included.
Agents are under the same
row-level security as everything else: an agent of another organisation does not exist for you.

In the console, **agents** (top bar) lists the registry and **your AI clients** — your `mcp:*`
tokens, each with its last call and the client that made it. A Claude Code that has called the door
shows as *connected*; **register as an external agent** creates the agent (with its human's tools,
or read-only ones) and attaches the token, and its calls then carry the agent. An agent's page gives
its last 30 days (runs, success rate, cost, today against its daily budget), its versions, a new
version started from the latest — where skills are attached or removed, each either at its latest
version (read at every run) or pinned to one — and, for an external agent, the clients acting as it. **skills**
is the library: import a zip or a repository (preview, pick, import at the resolved commit), each version's risk badge, scan findings and files, the approve button for a version waiting for review, an update check for a skill from git, and which agents carry it. A project's
**agents** tab shows what it pins and its **implicit agents**: workflow actors that run as agents
without naming one, which you can register, then name in the workflow (`agent: <slug>`).

### Models

`/admin/models` (the **Models** page of the administration) is the organisation's model catalogue
([ADR 0060](adr/0060-a-gateway-agnostic-model-catalogue.md)), whatever the gateway is. **Sync from
the gateway** reads the models it lists; **Add a model manually** covers one it routes but does not
list. Per model an administrator sets: enabled or not, the stage roles allowed (none checked = all),
the usages (`agent`, `embed`, `distill`, `eval`), a display name, and a price per 1k tokens that
replaces the gateway's in the ledger (only when the gateway reports tokens; a gateway that cannot read
a spend back leaves the line `unmeasured`).

- **Modes**: `open` (the default; everything the gateway lists is usable, the catalogue only
  restricts what it has an entry for) and `curated` (only enabled entries are usable; a model
  discovered later arrives disabled). Switching to `curated` shows first the agents and project
  profiles it would refuse.
- **Tiers**: the model behind `cheap`, `standard`, `strong`, `aux`, `embed` and `local` for the
  organisation. Resolution is project profile, then organisation tier, then the platform alias.
- **Refusals**: a disabled model cannot be named by an agent version (publication and trial answer
  422 naming it) nor mapped by a project profile, and a stage whose model was disabled after
  publication stops without a key and goes to a person (not retried). Embedding and distillation
  refuse a model that lost the `embed` / `distill` usage, and say to change it in `/admin/models`.
  `allow_unvalidated` never bypasses the catalogue.
- **Reading**: every member sees the page read-only and the agent builder offers only the usable
  models (`GET /orgs/{org}/models/usable`); changes need the `org_admin` role.

### Registering a remote agent (A2A)

An agent that runs elsewhere — Atlassian's Rovo, or any A2A agent — is registered as kind `remote`
([ADR 0062](adr/0062-remote-agents-over-a2a.md), set-up in [integrations](integrations.md)). Read its
card first: `POST /orgs/acme/agents/discover {"card_url": "https://…/.well-known/agent.json"}` lists its
skills, the credential types it accepts and suggests the entry (nothing is created). Then:

```bash
curl -X POST $API/orgs/acme/agents -d '{"slug": "rovo", "kind": "remote", "display_name": "Atlassian Rovo",
  "spec": {"instructions": "Answer from the team'"'"'s Jira and Confluence.",
           "runtime": {"kind": "a2a", "card_url": "https://a2a.atlassian.com/.well-known/agent.json",
                       "skill": "work-in-jira", "credential": "atlassian-rovo", "timeout_minutes": 10,
                       "declared_cost": {"per_task_usd": 0.05, "per_minute_usd": 0.0}}}}'
```

Publication reads the card and pins its digest; a skill the card does not have, or a credential of a
type the card does not accept, is a 422. A step names the agent as any other (`agent: rovo`). Declare
what the agent costs (`declared_cost`): the platform cannot measure it, so the ledger books your
declaration, apart from model spend, and says `unmeasured` when you declared nothing. A project that
uses it must allow the agent's host in `sandbox.network.allow_domains`.

### Knowledge bases

A **knowledge base** is a corpus you curate — an architecture framework, a security policy,
runbooks — that agents read and never write ([ADR 0051](adr/0051-knowledge-bases.md)). It is not
the memory: memory holds facts learned from work, proposed and accepted; a knowledge base holds
documents a human chose.

```bash
curl -X POST $API/orgs/acme/knowledge -H 'Content-Type: application/json' \
  -d '{"slug": "architecture", "name": "Architecture framework", "scope": "org", "serving_mode": "files"}'
curl -X POST $API/orgs/acme/knowledge/architecture/documents -F file=@zoning-principles.pptx
```

An upload (Word, PowerPoint, Excel, PDF, HTML, markdown, text; 20 MiB by default,
`CHOREGOS_KNOWLEDGE_MAX_UPLOAD_MIB`) is answered `202 pending` and converted to markdown **in the
background**, in a child process that sees none of the API's secrets and is stopped after
`CHOREGOS_KNOWLEDGE_CONVERSION_TIMEOUT_S` (120 s). Headings, slide titles and tables are kept; a PDF
gives its text without headings, and a scanned PDF fails (no OCR). Each document then reads
`ready` or `failed` with its reason, and `POST …/documents/{id}/convert` tries again. The same file
twice is one document. `scope` says who uses the base: `org`, `projects` (the listed ones) or
`groups` (people of the listed IdP groups, never a run). Curating needs `agent:manage` (an organisation administrator), or, for a base created from a project
(`POST /projects/{id}/knowledge`, `owner_project`), `project:write` on that project: its owners feed,
sync, distil and archive it, and only an administrator widens its scope. In the
console there are two ways in. **From a project**: its sidebar's **Knowledge** section lists what its
runs are served (the project's own bases, then the organisation-wide ones, read-only from there;
a group's base is never served to a run, so it is not listed), and **New knowledge base** creates one
served to this project's runs only (there is no scope to choose), owned by the project: its owners
(and organisation administrators) curate it; the base opens inside the project,
where you upload documents or add a source. **From the organisation**: **Knowledge** (`/knowledge`) is
the registry of every base, filterable by scope (organisation-wide, project, group), with who uses
each one; its creation form is where a base is shared with several projects (picked from a list) or
with groups. A base's page uploads documents and follows each conversion.

An agent version reads a base by naming it: `"knowledge": [{"slug": "architecture"}]` in its spec.
When a run is prepared, each named base must exist and be served to the run's project — otherwise
the run does not start (`KnowledgeRefused`). A base served as **files** has its ready documents
pinned by id and digest; the runner fetches exactly those (`GET /internal/runs/{id}/knowledge`),
checks the digest (a base changed in between stops the run, exit 51), and lays them read-only under
`.choregos/knowledge/<slug>/` with an `INDEX.md`, out of the diff. The prompt says where they are
and asks the agent to cite the document and section it applies.

A base served as an **index** is cut into contextual chunks (each prefixed by its document's title
and heading path) and embedded through the gateway (`default_model_profiles["embed"]`, else
`CHOREGOS_KNOWLEDGE_EMBEDDING_MODEL`, `platform/embed`; a deterministic fake under
`CHOREGOS_FAKES`). Agents search it with the sidecar's `knowledge_search` tool, which answers
numbered citations — document, section, excerpt — from the bases their run names, while those are
served to its project. The ranking fuses PostgreSQL full-text and pgvector by reciprocal rank
(`mode: hybrid`); without pgvector, or without a gateway key, it is full-text only and says so
(`mode: full-text`). `POST …/search` gives the same answer in the console; `POST …/reindex`
rebuilds the chunks. Each base keeps an **eval set** (`PUT …/eval`: questions and the documents,
by file name or title, expected to answer them); `POST …/eval/run` measures **recall@5** and the
base's page shows it, per question.

A base also **syncs from sources** (`POST …/sources`), each on its own Temporal loop
(`KnowledgeSync`; the refresh is set per source, see below; now with `POST …/sources/{id}/sync`, or
for the whole base with `POST …/knowledge/{slug}/sync`, **sync all now** on the page):

- **SharePoint or OneDrive**: create an organisation connector of capability `documents`, type
  `sharepoint` (`tenant_id`, `client_id`, `secret_refs.client_secret`), for an Entra application
  holding the application permission **`Sites.Selected`** — and have a tenant administrator grant it
  `read` on the chosen sites (`POST /sites/{site-id}/permissions`). Never `Files.Read.All`. The
  source names a `site_id` (or a OneDrive's `drive_id`) and optionally a `folder`; sync is delta.
  The console form **picks** the connector from a list of the organisation's `documents/sharepoint`
  connectors (it is never typed); with none, it links to `/admin/connectors?type=documents/sharepoint`
  to declare one. The API refuses a wrong name and says why: names are lowercase (the message
  suggests the right one), the connector is of another kind (`identity/entra`) or another type, or
  no `documents` connector exists yet (then an organisation administrator declares one).
- **A Confluence Cloud space or page tree**: create an organisation connector of capability
  `documents`, type `confluence` (`base_url` = `https://<site>.atlassian.net`, `email`,
  `secret_refs.api_token`). Use a **dedicated Atlassian service account** (or a Confluence-only
  user) holding **read** on the spaces to index and nothing else, and create its API token at
  `id.atlassian.com/manage-profile/security/api-tokens`. A classic token works with `auth: basic`
  (the default). A *scoped* token needs `auth: scoped` and the site's `cloud_id` (the connector's
  **test** reports it) and the scopes `read:space:confluence`, `read:page:confluence`,
  `read:hierarchical-content:confluence`, plus `read:attachment:confluence` when attachments are
  on. The source names a `space_key` (`ENG`; a personal space starts with `~`) and optionally a
  `page_id`: that page **and everything under it** (`include_root: false` leaves the page itself
  out). The page id is the number in the page's address, `…/wiki/spaces/ENG/pages/<id>/Title`.
  Options: `include_attachments` (pdf, docx, pptx, xlsx, md, txt, html), `labels` (keep only pages
  carrying one of them; one request per page). Each sync lists the space once; only pages whose
  version number changed are downloaded again. A page that is deleted, archived, **moved out of the
  chosen tree** or **restricted against the account** leaves the base at the next sync. A listing
  that fails part-way stops the sync and deletes nothing. The source mirrors what the connector's
  account can read: everything it can read in the space becomes readable by everyone the base is
  open to, whatever the page restrictions say for other people. A space larger than 5 000 pages is
  refused until a `page_id` narrows it. Signing in per person (OAuth 2.0, 3LO) is a possible
  follow-up; today the service account is the identity.
- **A web site**: an https `url`; same host, under its path, `robots.txt` obeyed, `max_depth` ≤ 5,
  `max_pages` ≤ 500; a host that resolves to a private address is refused. `exclude` is a list of
  path globs (at most 50, each starting with `/`, for example `["/es", "/es/*"]`) that are neither
  fetched nor followed. Redirects are followed inside the allow-list only; a start URL that
  redirects off it (`www.example.com` to `example.com`) is refused when the source is created, with
  the target to use instead. Pages are named by their whole path (`/en/about` is `en-about.html`).
  A crawl that lists no page at all (a moved site, a wall) is reported as unreadable and **deletes
  nothing**.
- **A git repository**: an https `url`, a `ref` (branch followed, or commit pinned), `paths` globs
  (`*.md` by default). A document is named by its whole path (`docs-adr-0001.md`), so two
  `README.md` do not collide; a repository where no file matches `paths` is reported unreadable and
  deletes nothing. A **private** repository is read through a `documents` connector of type `git`
  (`host`, default `github.com`, and `auth`): `auth: token` with a fine-grained access token
  (`Contents: read`) in the `token` secret field, or `auth: github_app` with the App's `app_id`, its
  `private_key` secret and optionally the `installation_id`, from which a one-hour installation
  token limited to that repository and `contents: read` is minted at each read. The source names the
  connector (`connector`); the repository's host must be the connector's host, so the credential is
  never sent elsewhere, and it travels to git in the environment, never in a command line.

**Refresh schedule.** A source refreshes in one of three modes (`refresh_mode`): `interval` (every
`interval_minutes`, 5 minutes to 7 days; the default), `cron` (`refresh_cron` with five fields, for
example `0 6 * * 1-5`, read in `refresh_timezone`, for example `Europe/Paris`; at least 5 minutes
between two fires) or `manual` (only when someone syncs). The page shows `next sync` beside each
source (an estimate: a worker that is down at the cron minute syncs at its next pass), and **edit
refresh** changes the mode, with **apply to all sources** to set the same schedule everywhere; the
change takes effect at once, not at the end of the current wait. A manual sync always runs.
#### A knowledge base as code

`choregos knowledge apply -f kb.yaml [--org <slug>] [--dry-run] [--prune]` makes a base match a
manifest: it creates or patches the base (name, description, scope, serving mode), its sources
(matched by `name`), its uploaded files and its eval set, and prints the plan (`+` create, `~`
change, `-` remove, `= n unchanged`). It is **idempotent**: a second run calls nothing that
changes anything. Uploads are local globs relative to the manifest, or `url:` entries downloaded
over https (at most 20 MiB); a file whose bytes changed replaces the previous document of the same
name. Without `--prune`, sources and uploads the manifest does not declare are left alone (the plan
says so). `--dry-run` reads and prints, changes nothing. An example about a fictitious company is in
`essai/knowledge/`. `choregos knowledge sync <slug>` syncs every source now, and
`choregos knowledge eval <slug>` runs the eval set and prints recall@5. The command needs an
organisation administrator's token (`agent:manage`). Refresh schedules in the manifest are the
source's `interval_minutes` today.

A document deleted at the source leaves the base **and its index** at the next sync. Each source
shows when it last synced, what it did (added, updated, deleted, skipped and why) and its last
error; each document links back to where it lives.
A base can be **distilled** into a principles skill (`POST …/distill`, **distill** on its page): the
`knowledge-distiller` agent of the catalogue — or the organisation's own version of it, once
installed and edited — reads the converted documents and answers principles, each citing the
document and section it comes from. The platform keeps only principles whose citations point into
the base (the others are listed as dropped), writes `SKILL.md` with each principle's sources and
`references/sources.md` with every document's sha256, and publishes the skill
`<base>-principles` as a new version **`pending_review`**, whatever its risk tier: no agent carries
it until an administrator approves it on the skill's page. One model call with the whole corpus in
the prompt: a base over 400 000 characters is refused.

## 4. Write the policy

```yaml
apiVersion: choregos/v1
kind: Policy
metadata: { name: team, version: 1, preset: team }
budgets:
  ticket_usd: { default: 8 }
  max_turns:  { implement: 40, verify: 20 }
  max_minutes: { implement: 30 }
  tool_calls_per_run: 20        # catalogue tools; omit for no cap
  daily_project_usd: 50
scope:
  max_diff_files: 60
sandbox:
  unknown_requests: reject      # refuse what the runner cannot classify (default: allow + journal)
  prompt_injection: block       # warn (default) journals; block stops the stage before any run
  suspend_max_hours: 24         # agent_sandbox only: how long a sandbox waits for an answer; 0 = never
```

Three presets ship (`solo`, `team`, `regulated`) and are a reasonable starting point.

**An organisation sets a ceiling that every project policy may only tighten**
(`PUT /orgs/{org}/policy-ceiling`, `org_admin`; **policy ceiling** on the admin page):

```json
{"budgets": {"ticket_usd": {"M": 20}, "stage_usd": {"default": {"M": 6}}, "max_turns": {"default": 60},
             "max_minutes": {"implement": 45}, "tool_calls_per_run": 50, "daily_project_usd": 100},
 "attempts": {"default_max": 2, "by_role": {"implement": 2}},
 "scope": {"max_diff_lines": 2000}, "findings": {"max_per_run": 3}}
```

A per-role table's `default` caps every role the ceiling does not name; a cap left out leaves projects
free. Values are compared as the engine applies them — a project policy that omits `ticket_usd` gets
the platform's defaults, and those are compared too. Saving a project policy above the ceiling is
refused (`422`, each widened field named, e.g. `budgets.ticket_usd.M: 30 exceeds the organisation's
ceiling of 20`). A policy saved before the ceiling was set is not refused: what runs is clamped to the
ceiling, the policy keeps what was written, and `GET /projects/{id}/policy` (and the project's
settings page) lists each clamp.

**Turn and loop limits are enforced by the runner**, not only written in the prompt. Each tool call
the agent makes counts as a turn: past the stage's `max_turns`, the runner cancels the ACP session
and the stage fails with `reason: max_turns`. The same tool with the same arguments
`CHOREGOS_RUNNER_LOOP_THRESHOLD` times in a row (5 by default; 0 disables) stops it with
`reason: loop`. Either way the agent's partial work is published like any failed stage's, and the
journal says `run.stopped`. When two consecutive attempts of a stage fail the same way (same
reason, same first line of summary, numbers aside), the interpreter escalates instead of spending
its remaining attempts.

**Fewer tokens per run** ([ADR 0055](adr/0055-fewer-tokens-per-run.md)) is off unless the policy
asks, and the deployment can still refuse it (`CHOREGOS_CONTEXT_REDUCTION=off`):

```yaml
context:
  shell_filter: rtk             # none (default) | rtk: shell output filtered before the agent reads it
  compression: headroom         # none (default) | headroom: tool results compressed, originals kept
  headroom_profile: coding      # coding | balanced | general
  on_unavailable: bypass        # the proxy does not start: bypass (journaled) or fail the stage
  rtk_exclude_commands: ["git diff"]   # left raw
  per_role:
    review: { compression: none }      # a reviewer reads raw tool results
```

The contract carries these switches; the runner does not act on them yet (stream S37).

**A policy does not decide who approves a work item.** People are declared in the **workflow**: a
transition `by` a human actor, or `train: {approval: <actor>}` on a release train
([ADR 0041](adr/0041-approval-follows-what-the-batch-ships.md)). The policy's own human gate is
the release train's: `release_train.<env>.approval` holds a departure until its group approves.

`approvals.*` (`spec`, `merge`, `prod`, `scope_change`) and `review.require_human_for_risk` are
still accepted, so an older policy stays valid, but **nothing enforces them**: no one is asked
([ADR 0044](adr/0044-people-are-declared-in-the-workflow.md)). Saving a policy that sets
them — a rule other than `required: never`, a non-empty list — logs a `policy.not_enforced`
warning per key, and the presets no longer write them. `review.cross_backend` is enforced.

## 4 bis. Governed actions

A write into a system that is not ours — create an account, order a laptop, deactivate a badge —
is a **governed action** ([ADR 0035](adr/0035-governed-actions-in-the-core.md)): proposed,
decided by people, then run by the platform in Temporal, **never inside the request that
approves it**.

```bash
curl -X POST $API/projects/acme:hr/actions -d '{"kind": "entra.create_user",
  "title": "Account for Léa", "params": {"upn": "lea@acme.example"},
  "effects": [
    {"effect": "connector.call",
     "with": {"connector": "entra-acme", "operation": "create_user",
              "arguments": {"upn": "{{ params.upn }}", "display_name": "Léa Martin"}},
     "compensate": {"effect": "connector.call",
                    "with": {"connector": "entra-acme", "operation": "disable_user",
                             "arguments": {"upn": "{{ params.upn }}"}}}},
    {"effect": "connector.call",
     "with": {"connector": "entra-acme", "operation": "add_to_group",
              "arguments": {"upn": "{{ params.upn }}", "group_id": "devs"}}}],
  "approval": {"approvers": [{"role": "project_owner", "min": 1}], "step_up_minutes": 10}}'
curl -X POST $API/projects/acme:hr/actions/<id>/decision -d '{"decision": "approve"}'
```

- **Deciding** takes a human session (an API token or an MCP client proposes, never decides), at
  least the rank an approver rule asks for, and — to approve — an authentication younger than
  `step_up_minutes` (else `401 step_up_required`, and the console re-authenticates). Whoever
  proposed, or owns the agent that proposed, does not decide. A rejection gives its reason. With
  `min: 2`, two different people approve. Of two concurrent approvals, one starts the action; the
  other gets `409`. An approver rule may also name a `group` (`{"role": "developer", "group":
  "netops-approvers"}`): the decider must then belong to that identity-provider group too, as its
  last OIDC sign-in stated it. The organisation names one per write operation with
  `PATCH /orgs/{org}/connectors/{name}/operations/{op}` `{"approver_group": "netops-approvers"}`,
  and the actions an agent proposes through it carry that rule (ADR 0052).
- **Running**: the approval starts `ActionWorkflow` (`action-<id>`) and answers at once. Each effect
  is recorded under its key (`<action>:<n>`) **before** it is attempted and confirmed after: a
  retried effect finds its key done and does nothing twice; one left started by a dead worker is
  attempted again — effects are idempotent by construction. A transient failure (`429`, `503`,
  network) is retried with backoff; a refusal (an account outside the administrative unit, a
  forbidden operation) stops the action, and what was done is **compensated in reverse** — the
  action says what it could not undo. Parameters are Jinja (sandboxed) over `params` and the results
  of earlier `effects`; a compensation also sees its own effect's `result`.
- **Proving**: an effect can make the action wait for the proof that it served — the next report
  of a collector, say — until a deadline: the action shows `awaiting_evidence`; a contrary proof, or
  none in time, fails it and compensates what was done.
- **Reading**: `GET /projects/{id}/actions/{id}` gives the decisions (who, when, how fresh their
  authentication was) and the **journal** — every effect's key, attempts, answer or error. Events
  `choregos.action.*` tell the same story on the ticket. In the console, every action has its page,
  `/p/<project>/actions/<id>`, where it is decided — an ontology proposal too: it is a core action
  under the same id, and its page says what it acts on (action type, targets, parameters), what its
  effects returned and what its evidence collected. The former `/p/<project>/proposals[/<id>]` links
  redirect there.
- **Waiting for a person**: the console's **inbox** (`/inbox`, top bar, with a count) lists what
  cannot move until someone decides, across the organisation's projects: tickets stopped on a human
  request (approval, question, scope change, task — overdue first, then the nearest due date, then
  the oldest), decided in place as on the board, and the actions waiting for approval. Groups are
  not checked yet: everyone sees everything that waits, including what is addressed to others. The
  former `/approvals` link redirects there. There are two inboxes: the organisation's, which a
  **Project** filter (`/inbox?project=<slug>`) narrows to one project, and each project's own
  (`/p/<slug>/inbox`, the **Inbox** entry of the project's sidebar), which lists and counts only
  that project's tickets, actions and project budget alerts. An agent's budget alert stays in the
  organisation's inbox, because an agent serves several projects.

The core ships two effects. `connector.call` is an operation of a connector of the organisation,
its key resolved by the platform, its arguments checked against the operation's schema; what an MCP
tool returns **structured** (`structuredContent`) is its result, so a later effect cites
`effects[0].serial` as it would for a typed connector. An `approval` operation runs here — the
action was approved; one this **project** cannot use — the organisation forbids it, the project
tightened it to `forbidden`, or its groups are not the project's — never does, and an action
calling it is refused when it is proposed (`422`). `check` is a check that touches nothing
outside: a condition, rendered with the earlier effects' results, that must be true — otherwise the
action fails, named by its `reason` (`condition: "{{ effects[1].active }}"`); it is `allowed`.
`verifier` (with `motif`) is the name it had before S39-11: it stays registered as an alias, so a
workflow or an action written with it keeps running; write `check` and `reason` from now on.

**An action at a date, from a workflow.** A system transition can propose the action itself. Its
title, justification and parameters are rendered (sandboxed Jinja) with the item's `fields` and the
`work_item`; `not_before` waits for a date read from a field the workflow declares as a date:

```yaml
metadata:
  inputs:
    type: object
    properties:
      upn: { type: string }
      start_date: { type: string, format: date }      # a date: midnight UTC; a date-time keeps its zone
transitions:
  - id: t-accounts
    from: planned
    to: accounts_ready
    by: platform                                      # an actor of type `system`
    action:
      kind: onboarding.accounts
      title: "Accounts for {{ fields.upn }}"
      params: { upn: "{{ fields.upn }}" }
      effects:
        - effect: connector.call
          with: { connector: entra-acme, operation: create_user,
                  arguments: { upn: "{{ params.upn }}", display_name: "{{ params.upn }}" } }
      not_before: fields.start_date - 10d             # d, h or m; + or -
    on_fail: { to: planned, max_attempts: 2, escalate_to: needs_review }
    on_reject: needs_review
```

- **The date moves, the timer follows.** `PATCH /work-items/{id}` with `{"fields": {...}}` replaces
  those fields (`null` removes one), validated by the item's own workflow, and tells its
  interpreter: a later date re-arms the timer, a date already past starts the action at once. A
  field not filled yet means waiting for it — no date, no action. A paused item does not start its
  action; it starts it when resumed.
- **Who approves.** When every operation the action calls — its compensations included — is
  `allowed` for the project and the workflow asks for no `approval`, the policy decides: the action
  is born approved (`by: policy`) and runs. Otherwise a person decides it in **Approvals**, like any
  other. A workflow can add an approval, never remove one. A plugin effect declares what it needs
  (`declarer_un_effet(name, fn, politique="allowed")`; `approval` by default).
- **What follows.** The transition passes when its action **succeeded** (`action_succeeded`, an
  implicit guarantee). Rejected, it follows `on_reject` when there is one; failed — compensated —,
  rejected without `on_reject`, or impossible to propose (a missing field, a forbidden operation),
  it follows `on_fail`, which such a transition must declare: without it the item would propose the
  same action forever. An unknown effect is refused when the workflow is **published**.
- **How the item learns.** The action runs in its own `ActionWorkflow`; when it settles, it signals
  the interpreter that proposed it (`action_settled`), and a rejection does too. The item also
  re-reads the action every six hours, in case a signal was lost.

**A task, with its proof.** Some steps are gestures a person makes — handing over a badge,
unpacking a laptop. A human transition can be a **task**: what to do, a form whose properties are
fields of the item, and a sentence the person attests.

```yaml
  - id: t-badge
    from: badge_due
    to: badge_handed
    by: front_desk                                    # an actor of type `human`
    task:
      title: Hand the badge over
      instructions: In person, then tap it on the reader.
      form:
        type: object
        required: [badge_uid]
        properties: { badge_uid: { type: string, minLength: 8 } }
      attest: I handed the badge to its holder in person
```

The item page shows the form; **done** stays closed until every required field is filled and the
sentence is attested. `POST /work-items/{id}/decisions` with `{"kind": "complete", "values": {...},
"attested": true}` accepts only what the form describes, refuses a missing or malformed field (`422`,
with its path) and a missing attestation, and writes the values into the item's **fields** — checked
again by `metadata.inputs`, which keeps the last word. The next action reads them:
`params: { uid: "{{ fields.badge_uid }}" }`. The decision keeps the values and the attested sentence
as it was shown, and the timeline tells it: that is the proof. A task that cannot be done is sent
back with its reason (`reject`). The validator wants a `human` actor, an object form, and every
form property declared in `metadata.inputs`.

**A tool under approval becomes an action.** A run sees an `approval` operation like any tool,
its description saying it needs a human approval. Calling it reaches **nothing**: it proposes a
governed action in the agent's name (`agent:<slug>`, origin `tool`), returns its id and the
console path where it is decided, and counts against the run's `tool_calls_per_run` — an agent
in a loop does not fill the box. The agent's **owner** does not approve its agent's writes.

**Approvals** (top bar) lists what waits across the organisation's projects
(`GET /orgs/{org}/actions?status=pending_approval`); a project's **actions** tab lists its actions,
and each one shows its parameters, effects and their compensations, its decisions — who, when, how
long after signing in — and the journal, key by key.

## 5. Let a ticket run

Label a ticket `agent-ready` in your tracker (or create it in Choregos with
`tracker: internal`). The platform starts one workflow per ticket, with a deterministic id —
so a webhook delivered twice starts nothing twice. If a webhook is lost, a reconciliation
pass every 60 seconds catches up.

You can also file a work item yourself. On a project's board, **New work item** opens a drawer: the workflow, the inputs that workflow declares, a title, a description, a size and an optional budget (which only tightens the workflow's). MCP does the same with `create_work_item`. With
an internal tracker (or none) the platform mints the key. With GitHub, Jira or GitLab it creates
the issue there first, labelled `source:choregos` and never `agent-ready`, and records the work
item under the issue's key, with its creation inputs, workflow and budget (ADR 0059). The tracker's
credentials need the right to create issues (GitHub App: *Issues: read and write*; Jira: create
issue on the project; GitLab: `api` scope). If the tracker refuses, you get a 502 and nothing is created.

```bash
choregos items list billing-api
choregos runs tail <run-id>        # live ACP log over SSE
```

### Work items on a schedule

With `tracker: internal`, a project can open work items by itself (S38-11):

```yaml
schedules:
  - name: weekly
    cron: "0 6 * * mon"          # five fields, UTC
    title: Weekly campaign
    fields: { symbols: [SPY.US, QQQ.US], start: "2018-01-01", oos_start: "2024-01-01", end: "{window}" }
    workflow: campaign           # optional; the project's routing otherwise
```

The reconciliation pass opens the work item of the current window, keyed by the window
(`RESEARCH-weekly-20261019T0600Z`), then starts it. Two passes in the same window find the key and
open nothing more. `{window}` in a text field becomes the window's date. A window missed by more than an hour — the workers were down — is skipped, not run
late. With an external tracker, or with the reconciliation loop off
(`reconcile_interval_seconds: 0`), nothing fires. Fields the workflow refuses are logged and the
pass reports the schedule under `refused`; the other schedules still fire.

### One child work item per element, then a join

A system transition can open one child work item per element of a list and pass once every child
is settled (S38-12, ADR 0057):

```yaml
- id: t-review-each
  from: screened
  to: decided
  by: platform                       # a `system` actor; the transition does nothing else
  spawn:
    workflow: review                 # the children's workflow, in the same project
    for_each: outputs.metrics.relevant   # or fields.<name>
    title: "Review {{ item.symbol }}"
    fields: { symbol: "{{ item.symbol }}", dsr: "{{ item.dsr }}" }   # one expression keeps its type
    max_children: 20                 # a longer list is refused
    succeeded: [adopted]             # the children's states that count as a success
    min_succeeded: 1                 # fewer fails the join
  on_fail: { to: screened, max_attempts: 1, escalate_to: nothing_adopted }   # required
```

The children are keyed by the parent's key and their rank (`RESEARCH-12.1`): a retry or a replay
finds them and opens none twice. Each child signals its parent as it closes, and the parent
re-reads its children every 30 minutes in case a signal is lost; its status says how many are still
open. The join writes `documents.children` on the parent: the count per state, and each child's key,
title, final state and fields. A list that cannot be read, too long, or a child its workflow refuses,
follows `on_fail`. Children need the internal tracker; the local mode stops at a fan-out.

## 6. Read what came out

In the UI, a run shows its cost, its duration, its **evidence**, its scope, its full ACP
journal, the diff and the artefacts. Evidence is what gates read: tests actually executed,
lint, typing, coverage — or, for work that has none of those, the facts the business named.

Next to the journal, the **pod logs** tab (`GET /runs/{id}/logs`) shows what happened to the
runner's pod: the last 200 lines of its log, secrets masked, and how it ended — exit code and
the runtime's reason (`OOMKilled`, `ErrImagePull`…). It is what explains a run that failed
before the agent said anything. While the pod runs the tail is a snapshot the orchestrator
refreshes at each poll (every `heartbeat_seconds`, 60 s by default), not a stream; once the run
ends it is archived with the run (ADR 0054).

When an agent is blocked it does not guess: it asks, the ticket moves to a waiting state, a
human is notified with an SLA, and the answer resumes the workflow where it stopped.

A ticket's whole path reads in one call: `GET /work-items/{id}/journey` returns the map of the
workflow version the ticket is pinned to, every move from state to state with the edge that
carried it (`nominal`, `reject`, `changes_requested`, `retry`, `escalate`, `default`, `resume`…),
and every step an actor took on each transition — agent runs with their verdict and evidence,
requests to people and what they decided, governed actions, release-train departures that carry
the ticket.

The work item's page draws that journey and animates it. The workflow's path snakes across the
page; each step is a shape for who carries it — a circle for an agent, a circle with a person for a
human decision, a square for the platform, a pill for the release train — and says in words what it
is doing: in progress (turquoise, pulsing), waiting for a person (pulsing slowly), sent back (amber),
done (green), failed (red). A review that asks for changes, a test run that fails, a rejection draw
an amber arc and count their rounds ("approved in round 2"); a detour — addressing a review, fixing
the CI — hangs under the step that sends there. **Live** follows the work item as it moves;
**Replay** plays its life back event by event at 0.5×, 1× or 2×, and a stage at the bottom jumps
there. Clicking a step opens its panel: one tab per attempt, what the agent did (gates, access with
refusals first, files changed), its log — live while it runs — and, for a person still awaited, the
decision itself.

## 7. Day-2 knobs

| Symptom | Knob |
| :-- | :-- |
| Bursts of agent pods | `choregos-orchestrator.runner.maxActive` |
| An agent pod refused with no message | `runner.limits` above the namespace `LimitRange` |
| Spend drifting | `budgets.daily_project_usd`, `ticket_usd`, alert at `alert_at_ratio` |
| Third-party API spend | `budgets.tool_calls_per_run`, cost report grouped by `kind` |
| An agent wandering outside its scope | `scope.max_diff_files`, gate `scope_respected` |
| Too many findings from one run | `findings.max_per_run` |

Costs export as CSV from the project overview, grouped by day, stage, model, backend, size
or kind.

**Renaming an organisation.** `PATCH /orgs/{org}` changes its `name` (its administrator) or its
`slug` (the platform administrator only: the slug is in every console URL, every qualified id
`<org>:<project>`, every MCP address `/mcp/projects/<org>:<project>`, and the IdP group names
`choregos:<org>:<group>`). Projects, members, tokens and everything below follow — they hold the
organisation by its id. What does not follow: addresses already copied elsewhere (an MCP client's
configuration), IdP groups that still name the old slug, and `oidc.defaultOrg` if it names it.

```bash
curl -X PATCH $API/orgs/acme -d '{"slug": "acme-corp", "name": "Acme Corporation"}'
```

## Known limits, stated rather than discovered

- The engine is still shaped by software in two places (ADR 0012): `StageOutputs` names
  development fields — business outputs travel by name in `outputs`, are stored on the ticket as
  artifacts and handed to the next stage as `inputs`, typed by `output_types` when they are files
  (ADR 0049) —, and most gates read a diff. `outputs_present` and `evidence_facts` are the
  business-side gates.
- The tool catalogue is listed to agents and callable, but nothing *requires* a call: a
  playbook can announce a tool the agent ignores. A `tool_called` gate reading the ledger is
  the mechanism to write (P1-6).
- Human-in-the-loop is a state with a deadline and an escalation, and a decision bar in the
  front; there is no checkpoint/resume of the agent's own context across the wait.
- The single-node bench measures **no cost** unless the embedded gateway gets a provider
  key: in *direct* mode the agent brings its own credentials and nothing is metered.
- `docs/plan/BLOCKERS.md` (French) keeps the rest, with causes and workarounds.
