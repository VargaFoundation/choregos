# Plan — 2026-10-27: agents ship as plugins; ServiceNow is the first

Source: the product owner's requests of 2026-10-27: "plan an agent for ServiceNow", then "make it a
complete plugin that embeds its agent, in a format the platform understands — the same for every future
agent; create an open-source repository of agents packaged in that format; a deployment lists the
plugins it installs by default, and plugins from other repositories must be possible".
Checked against `main` at `7919257b`. Stream number: **S43**. ADR numbers reserved: **0093**, **0096** (0094 was taken by S44-01 meanwhile)
(0088-0092 are taken by open pull requests).

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

Scope note: this repository is public. Nothing here names a customer's ServiceNow instance; the live
proof runs on a ServiceNow Personal Developer Instance (PDI), which anyone can obtain.

---

## 0. What the audit found before planning

| Question | What the code says | Verdict |
|:--|:--|:--|
| Is there a package format for an agent with what it needs? | No. A template (`templates/<name>/manifest.yaml`) ships agents, skills, workflows and extensions, but only from the image's `templates/` directory; a template published through `POST /templates` "carries no file", so it cannot ship agents or skills | Missing |
| Can an agent come from outside the image? | No. The catalogue (`choregos_core/catalogue_d_agents/agents/*.yaml`, 23 entries) is a package directory; no override, no source | Missing |
| Can anything be installed from git or a registry? | Skills only: a zip upload or a GitHub/GitLab tarball pinned to a commit, behind a per-organisation source allowlist and the supply-chain gate (ADR 0050). The MCP catalogue takes one operator file (`mcpCatalogueExtra`) | Partial, reusable |
| Can connector code be added without rebuilding images? | No. `choregos.plugins` wheels load only from a derived image (the enterprise edition does exactly that). An org `mcp` connector reaches a server by URL; its discovered tools start closed and the broker governs each one (`ConnectorOperation`, `human_floor`, governed actions ADR 0035/0085) | Reusable: an out-of-process MCP server is governed today |
| Does the platform run third-party images? | Hosted agents (ADR 0080): registry allowlist, digest pinning, cosign `off`/`warn`/`enforce` — inside a run's pod, not as a long-lived service | Partial, rules reusable |
| Does Choregos know ServiceNow? | No occurrence. One conditional line of the bundled `runbook` skill ("If ~~ITSM is connected") | Missing |

Conclusion: the platform governs what a plugin would bring (agents, skills, templates, MCP tools under
approval). What is missing is the **package**, its **sources and trust**, the **installer**, and a way to
**run a plugin's MCP server** without the operator building an image.

## 1. Decisions

### Decision 1 — the format: `kind: Plugin`
A plugin is a directory with `plugin.yaml` at its root (`apiVersion: choregos/v1`, `kind: Plugin`):

```yaml
apiVersion: choregos/v1
kind: Plugin
metadata:
  name: servicenow            # [a-z0-9-], unique within a source
  version: 0.1.0              # semver; a published version is immutable
  publisher: Varga Foundation
  license: Apache-2.0
  description: Triage ServiceNow incidents under human approval.
  homepage: https://github.com/VargaFoundation/choregos-agents
  core: ">=0.21.0,<0.23.0"    # the core versions it was validated against
requires:
  plugins: []                 # other plugins, by name and range
  connectors: {}              # project connector types, as in a template
contents:
  agents: [agents/itsm-triager.yaml]                # today's catalogue entry shape
  skills: [skills/servicenow-triage]                # SKILL.md directories
  templates: [templates/itsm-incident-triage]       # today's template shape, files included
  evals: [evals/itsm-triager.yaml]
  procedures: [procedures/*.md]                     # ADR 0067 front matter
  servers: [servers/servicenow.yaml]                # Decision 2
```

Paths cannot leave the directory; symlinks are refused (the template rules). An agent entry keeps the
catalogue shape (`catalogue:` + `agent:` = `AgentCreate`), so the 23 built-in agents move without being
rewritten. The Python entry-point group `choregos.plugins` keeps its role; the documentation calls those
**extensions** from now on, so that "plugin" means one thing to users.

### Decision 2 — plugin servers: connector code runs out of process
A plugin brings code only as an **MCP server image** (`servers/<name>.yaml`):

```yaml
name: servicenow
image: ghcr.io/vargafoundation/choregos-agents/servicenow-mcp@sha256:…   # digest required
port: 8080
protocol: mcp-http
config_schema: {…}               # what an administrator types: base_url, client_id, groups…
secret_fields: [client_secret, webhook_secret]
egress: {from_fields: [base_url]}
operations:                      # the governance floor; a tool not listed here is refused
  - {name: search_records, access: read}
  - {name: add_work_note, access: write}
  - {name: add_comment, access: write, human_approval: true}
  - {name: resolve_incident, access: write, human_approval: true, compensation: reopen_incident}
implements: [tracker]            # Decision 3
webhook: {path: /events}
```

The platform runs it: a Deployment and a Service in a dedicated namespace (`choregos-plugins`), created
by the API with a Role limited to that namespace, a NetworkPolicy that admits only the API and the
server's declared egress, and the hosted-agent image rules (registry allowlist, digest, cosign).
Installing the plugin creates an organisation `mcp` connector pointing at the Service; `operations` become
`ConnectorOperation` rows whose policy the broker enforces exactly as for pfSense today. Secrets are
sealed (ADR 0058) and reach the server through the broker's call, never a run's pod.

### Decision 3 — a plugin can be a tracker
A server declaring `implements: [tracker]` serves a fixed set of tools: `tracker_fetch_item`,
`tracker_create_item`, `tracker_set_state`, `tracker_comment`, `tracker_upsert_status`, `tracker_close`,
`tracker_list_candidates`, `tracker_parse_event`. One core adapter, `tracker/plugin`, maps
`TrackerAdapter` onto them. Inbound events arrive on `POST /webhooks/plugins/{plugin}/{server}`; the core
forwards the delivery to the server's `webhook.path`, which verifies it and returns normalised events;
the core de-duplicates and re-reads each item before acting.

### Decision 4 — distribution and trust
- A published plugin is an **OCI artifact** (`application/vnd.choregos.plugin.v1.tar+gzip`), signed with
  cosign (keyless, the publisher's CI identity), or a **git reference pinned to a commit**.
- A **source** is an index (`index.yaml`: name → versions → digest, `core` range) and a trust policy
  (a cosign identity and issuer, or a public key). Anyone can publish a source from their own repository.
- Every install goes through the supply-chain gate of skills (ADR 0050): digest checked, signature
  verified against the source's trust policy, contents validated, a second approval for a plugin that
  brings a server.

### Decision 5 — what a deployment installs by default
The chart gains:

```yaml
plugins:
  sources:
    - {name: varga, index: oci://ghcr.io/vargafoundation/choregos-agents/index,
       trust: {issuer: https://token.actions.githubusercontent.com,
               identity: https://github.com/VargaFoundation/choregos-agents/.github/workflows/publish.yml@refs/heads/main}}
    - {name: acme, index: https://git.acme.example/agents/index.yaml, trust: {key: <cosign public key>}}
  defaultPlugins:
    - {source: varga, name: software-delivery, version: "^1"}
    - {source: varga, name: servicenow, version: "^0.1"}
  offline: /opt/choregos/plugins     # plugins baked into the image (air-gapped, demo, CI)
```

The API reconciles at start-up, idempotently: each default plugin is installed in every organisation.
Other plugins are installed from the console or the API by whoever ADR 0074 allows. Upgrades keep the
catalogue rules of ADR 0040: an agent an organisation changed is offered the update, never overwritten;
project pins do not move.

### Decision 6 — the built-in agents leave the core
The 23 catalogue agents move to the open-source repository **`VargaFoundation/choregos-agents`**,
grouped in domain plugins (for instance `software-delivery`, `netops`, `research`, `knowledge`,
`clients`). The core keeps the loader and loses the YAML; its default values list these plugins; the
image build bakes a `plugins.lock` snapshot into `offline`, so `make demo`, `CHOREGOS_FAKES=1` and CI stay
offline. A data migration rewrites provenance `catalogue:<slug>@<v>` into `plugin:<plugin>/<slug>@<v>`,
so organisations that installed them keep receiving upgrades.

### Decision 7 — the ServiceNow plugin
In `choregos-agents/plugins/servicenow`:
- **Server** (Python, MCP over HTTP): ServiceNow Table API, OAuth 2.0 client credentials (basic
  authentication of a dedicated user for older releases and PDIs), a closed list of tables (`incident`,
  `sc_req_item`, `change_request`, `problem`, `kb_knowledge`, `cmdb_ci`, `sys_user_group`), typed filters
  turned into encoded queries (never a raw query string), state codes read from `sys_choice`, a built-in
  fake mode for tests and demos.
- **Operations**: reads allowed (`search_records`, `get_record`, `get_journal`, `get_ci`,
  `list_ci_relations`, `search_knowledge`, `similar_incidents`); `add_work_note` and `update_record`
  under approval; `add_comment` (customer-visible), `resolve_incident` and `create_change_request` always
  need a person. Priority is never written (ServiceNow computes it from impact and urgency).
- **Tracker**: incidents, request items and changes as work items; inbound events from a Business Rule
  (script shipped in the procedure) posting with a shared secret; polling of the assignment queue when
  there is none.
- **Agent `itsm-triager`**, modelled on `netops`: reads, cites similar incidents and knowledge articles,
  proposes classification and actions that a person approves. Evals: a duplicate, a vague incident (must
  not resolve), a VIP caller (no comment without approval), an instruction injected in the description
  (must not obey — ADR 0084).
- **Template `itsm-incident-triage`**: incident → triage (agent) → review (service desk) → apply → done.
- **Procedures**: the OAuth inbound integration, a custom role (no `admin`), the Business Rule.
- Phase 2: agent `change-author` and a "change approved" gate before production in
  `software-delivery-governed`.

## 2. Stories and order

Core — `VargaFoundation/choregos`:

| Story | Title | Depends on |
|:--|:--|:--|
| S43-00 | This plan | — |
| S43-01 | `contract(schemas)`: `plugin.schema.json`, `plugin-server.schema.json`, OpenAPI (`/orgs/{org}/plugins`, `/plugin-sources`, install, upgrade, uninstall); ADR 0093 | 00 |
| S43-02 | Loader and validator (`choregos_core/plugins/`), CLI `choregos plugin validate / pack / index` | 01 |
| S43-03 | Sources and trust: OCI pull and cosign verification, pinned git, offline directory, supply-chain gate, fakes | 02 |
| S43-04 | Installer: agents (catalogue rules), skills, templates **with their files**, evals, procedures; provenance; uninstall | 02 |
| S43-05 | Chart `plugins.{sources, defaultPlugins, offline}`, start-up reconcile | 03, 04 |
| S43-06 | Plugin servers: run, network, image rules, operations as the governance floor; ADR 0096 | 04 |
| S43-07 | `tracker/plugin` adapter, `POST /webhooks/plugins/{plugin}/{server}`, replayed history | 06 |
| S43-08 | Console: Catalogue → Plugins (sources, contents, versions, install, upgrade, rights) | 04 |
| S43-09 | The built-in agents leave the core: provenance migration, baked lock, default values | A-03 |

New repository — `VargaFoundation/choregos-agents` (public, Apache-2.0, DCO):

| Story | Title | Depends on |
|:--|:--|:--|
| A-01 | Layout, generated index, CI: validate with the core CLI, test servers, build images, sign, publish to ghcr; a guide for third-party sources | S43-02 |
| A-02 | Plugin `servicenow` (Decision 7), live tests against a PDI | A-01, S43-06, S43-07 |
| A-03 | The 23 built-in agents as domain plugins | A-01 |
| A-04 | Phase 2: `change-author`, the change-approved gate | A-02 |

```
S43-00 ─► 01 ─► 02 ─┬► 03 ─┐
                    ├► 04 ─┼► 05
                    │      ├► 06 ─► 07 ─► A-02 ─► A-04
                    │      └► 08
                    └► A-01 ─► A-03 ─► S43-09
```

Acceptance shared by every story: `make ci` green; a STATUS row saying what the PR proves and does not
prove; each guard has a test that fails without it — an unsigned or wrongly signed plugin refused, a path
escaping the plugin refused, a tool not declared in `operations` refused, `human_approval` never lowered,
a start-up reconcile run twice changing nothing, provenance migrated without bypassing RLS.

## 3. ADRs taken in this stream

| ADR | Title | Amends |
|:--|:--|:--|
| 0093 | An agent ships as a plugin: one format, signed sources, default plugins per deployment | 0040 (the catalogue becomes plugins), 0065, 0067 (procedures come with their plugin) |
| 0096 | A plugin's code runs as an MCP server the platform runs and governs; a plugin can be a tracker | 0034, 0080 (image rules shared) |

## 4. What stays with the operator

- Merges into `main` and the releases that carry the stream.
- On each cluster that runs plugin servers: the `choregos-plugins` namespace, its network rules, and the
  registry allowlist for `ghcr.io/vargafoundation/choregos-agents`.
- For the live ServiceNow proof: a PDI, its OAuth inbound integration, the integration user with the
  custom role, their values in the operator's secret store, the Business Rule, and the egress rule for the
  instance host.
- Phase 2 needs an organisation that really runs a change advisory board to say which change template
  and which approval states count as "approved".
