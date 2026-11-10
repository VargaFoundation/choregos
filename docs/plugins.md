# Writing a plugin

A plugin packages agents and what they need, in one format the platform understands
([ADR 0093](adr/0093-an-agent-ships-as-a-plugin.md)). This page covers writing one and checking it
locally with the CLI, how a deployment declares the sources it fetches plugins from and how it trusts
them, what installing one into an organisation does, and the plugins a deployment installs by default.

"Plugin" here means this package. A Python wheel loaded through the `choregos.plugins` entry point is an
**extension**.

## The layout

```
hello/
├── plugin.yaml                      # the manifest (plugin.schema.json)
├── agents/greeter.yaml              # a catalogue entry: `catalogue:` + `agent:`
├── skills/greeting/SKILL.md         # a skill directory
├── templates/hello-flow/manifest.yaml   # a template, with its files
├── evals/greeter.yaml               # an eval suite naming one of the plugin's agents
├── evals/matrix/greeting.yaml       # a matrix case of the core's eval matrix (`matrix_cases`, #1372)
├── procedures/connector.mcp.hello.md    # a procedure (ADR 0067 front matter)
└── servers/hello.yaml               # an MCP server the platform runs (plugin-server.schema.json)
```

A complete example lives in `tests/fixtures/plugins/hello/`; `tests/fixtures/plugins/servicenow/` ships
matrix cases and a server with a `fake`.

**A plugin is what its manifest declares**: `plugin.yaml`, a `README.md` and a `LICENSE` at its root when
present, and exactly the paths `contents` names — each file, every file under a skill or template
directory, every file a procedure pattern matches, and what an assistant names (its knowledge seeds, task
workflows and eval suites, [Assistants](#assistants)). Anything else in the directory (a server's source, its
`.venv`, build output) is not read, not validated, not digested and not packed, so a server's code can
live beside its plugin without shipping in it.

```yaml
apiVersion: choregos/v1
kind: Plugin
metadata:
  name: hello                 # [a-z0-9-], unique within a source
  version: 1.0.0              # semver; a published version is immutable
  publisher: Varga Foundation
  license: Apache-2.0
  description: A test plugin that ships one of everything a plugin may ship.
  homepage: https://github.com/VargaFoundation/choregos
  core: ">=0.20.0"            # the core versions it was validated against
requires:
  plugins: []                 # [{name, version: "^1"}]
  connectors: {}              # project connectors, as in a template
contents:
  agents: [agents/greeter.yaml]
  skills: [skills/greeting]
  templates: [templates/hello-flow]
  evals: [evals/greeter.yaml]
  procedures: ["procedures/*.md"]   # the only list that may use *
  servers: [servers/hello.yaml]
  # matrix_cases: [evals/matrix/greeting.yaml]   # agent cases of the core's eval matrix (#1372)
  # assistants: [assistants/helper.yaml]        # assistants wrapping an agent of the plugin (ADR 0133)
```

## What is checked

`choregos plugin validate` reports every problem it finds, each with its file:

- **Paths stay inside the plugin.** A path cannot be absolute or hold a `.` or `..` segment, and no
  declared path, nor anything under a declared directory, may be or pass through a symbolic link, even
  one pointing inside the plugin. These are the template rules, shared in `choregos_core.paths`. A link
  elsewhere in the directory is ignored with the rest of what is not declared.
- **The manifest** matches `plugin.schema.json`. A kind other than `Plugin`, a version that is not
  semver, or an unknown key is refused.
- **Agents** have the shape of a built-in catalogue entry (ADR 0040): `catalogue.version`, `role` and
  `summary`; `agent.slug`, `display_name` and `spec`. The full `AgentCreate` validation runs when the
  plugin is installed.
- **Skills** are directories that hold a `SKILL.md`.
- **Templates** have a `manifest.yaml` that matches `template.schema.json`. A template whose project
  tracker is a server of the plugin says `requires.connectors.tracker: plugin:<plugin>/<server>`; the
  server must be one of the same plugin and declare `implements: [tracker]` (see
  [The tracker profile](#the-tracker-profile)).
  A template may ship the knowledge bases its agents read (`defaults.knowledge`, see
  [Knowledge bases](#knowledge-bases)); a source that reads a template input must name an input the
  template declares.
- **Evals** match `plugin-evals.schema.json` and name an agent of the plugin (see [Evals](#evals)).
- **Matrix cases** (`contents.matrix_cases`) are agent cases of the core's eval matrix, in the format
  of the core's own (see [Matrix cases](#matrix-cases)): a mapping with a `task`, an `agent` of the
  plugin (or the case's own `agent_spec`), `tools_cells` among `mcp` and `scripts`, and every connector
  `plugin:<plugin>/<server>` naming this plugin and one of its servers, under that server's name; no two
  cases of one file name.
- **Procedures** go through the same parser as the core's (ADR 0067): front matter, fixed sections,
  links listed in `vendor_docs`, no secret-shaped string. A procedure gives the address of its server's
  webhook with `{{plugin_webhook_url:<plugin>/<server>}}`, filled with
  `<api>/api/v1/webhooks/plugins/<org>/<plugin>/<server>`; it may name only a server of its own plugin that
  declares a `webhook`.
- **Servers** match `plugin-server.schema.json`:
  - the image is pinned by `@sha256:`;
  - every operation states its `access` (`read` or `write`);
  - a read has no `compensation`, and a compensation names an operation of the same server;
  - `paths` (`mcp`, `health`; defaults `/mcp` and `/healthz`) name where the server answers;
  - `platform_arguments` (`action_key`, `work_item_key`) are arguments the platform fills, never the
    agent;
  - operation names are unique;
  - an egress `from_fields` entry is a field of `config_schema`;
  - a server that `implements: [tracker]` declares every tool of the
    [tracker profile](#the-tracker-profile), each with the profile's access;
  - a server's `fake` ([On its fake](#on-its-fake-for-the-eval-matrix)) switches it with variables
    that are neither `CHOREGOS_*` nor any its configuration reaches.

  An operation's `human_approval` is a floor: an organisation may raise it, never lower it. A tool the
  server serves but does not list is refused.

Validation also **warns**, without refusing: an agent that reads a knowledge base (`spec.knowledge`) that
no template of the plugin ships runs only in an organisation that already has that base and serves it to
the project — its runs stop otherwise. `choregos plugin validate` prints each warning after the digest
(`! agent `x` reads the knowledge base `y`, which no template of this plugin ships …`).

### Knowledge bases

An agent names the bases it reads by slug, and its run stops when a base does not exist or is not served
to its project. A plugin ships the base with the template that brings the agent
([ADR 0117](adr/0117-a-template-ships-the-knowledge-bases-its-agents-read.md)), in the shape of a
`kind: KnowledgeBase` manifest less what the project decides (scope, uploads, eval set):

```yaml
# templates/hello-flow/manifest.yaml
defaults:
  workflow: template:default-simple@1
  knowledge:
    - slug: greeting-docs              # what the agents name in spec.knowledge
      name: Greeting documentation
      serving_mode: files              # or index
      sources:
        - name: Repository docs
          kind: git                    # git only
          config:
            url_from_input: repo       # or url: https://github.com/acme/handbook
            ref: main
            paths: ["docs/**/*.md"]
          interval_minutes: 60         # or refresh_mode: cron, refresh_cron, refresh_timezone
inputs:
  - { name: repo, type: github-repo }
```

A project born of the template gets each base owned by it and served to it, its sources attached and
synced at once. A `github-repo` input left empty is the project's repository, read through the
deployment's GitHub App with a read-only token; another private repository names an organisation
`documents/git` connector (`connector:`). A base of that slug the organisation already has is reused when it
is served to the project and never rewritten; otherwise the project is born without it and the audit log
says why (`template.knowledge.skip`). [Knowledge bases from a template](usage.md#knowledge-bases-from-a-template)
has the full rules.

### Evals

An eval file (`plugin-evals.schema.json`) has the shape of the catalogue's cases (the core's own until
S43-09, now the `evals/` of the built-in plugins), plus the API's `AgentEvalCase` list:

```yaml
agent: greeter                     # an agent of this plugin
description: The greeting names the author.
must_contain: [greet the author]   # level 1: in the rendered instructions, case-insensitive
must_not_contain: [without approval]
min_score: 1.0                     # optional, the share of must_contain to find
cases:                             # optional, AgentEvalCase
  - name: names-the-author
    input: {title: Say hello, body: Greet Ada.}
    must_contain: [Greet the author]   # case-sensitive, on the instructions rendered on `input`
  - name: names-the-reporter
    input:
      title: Say hello
      fields:                      # optional, rendered as `ticket.fields` (S51-10)
        tracker: {kind: jira, key: FON-270, reporter: {display_name: Ada Lovelace}}
    must_contain: [Ada Lovelace]   # when the instructions say {{ ticket.fields.tracker.reporter.display_name }}
  - name: live-greeting
    live: true                     # a capped trial on a real model (S40-02d)
    expect_status: done
    max_cost_usd: 0.25
    must_contain: [Hello]
```

An unknown key is refused, and a suite holds `must_contain`, `cases` or both. A case's `input` is the
API's `AgentSampleInput`: `title`, `body` and `fields`, the work item's fields the instructions see as
`ticket.fields` (`{}` when absent), at most 64 KiB of JSON. `fields.tracker` plays a ticket filed from a
tracker (ADR 0118); a plugin's case may set it, a caller's work item may not. The installer plays level 1
and every case that is not `live`; a failure refuses the plugin. A `live` case is validated, not run at
install.

### Matrix cases

A matrix case is an agent case of the core's eval matrix (S57-09; [the eval matrix
runbook](runbooks/live-eval-matrix.md#a-plugins-cases-1372)), in the same format as the core's own
(`choregos_playbooks/evals/agent_cases/*.yaml`): its agent, its task, its doors (`tools_cells`), its
connectors, its evidence, its scripted script and answers, its assertions. The matrix names it
`plugin:<plugin>/<file stem>` (`plugin:servicenow/itsm-triage-similar-incidents`). A connector may be
one of the plugin's servers:

```yaml
connectors:
  servicenow: { kind: "plugin:servicenow/servicenow", scene: default }   # named after the server
```

The installer keeps matrix cases and never plays them: the matrix does, in a scripted cell against the
server's fake (in the core's CI, a fake registered for the server) and in a live cell against the
server's eval instance.

A case judges the task, not the door (S57-11b, [What a case checks of its
run](runbooks/live-eval-matrix.md#what-a-case-checks-of-its-run)): its facts are the answer's, its
guarantees the run's (actions proposed and executed, tools never called, security events). What a door
costs — `calls_by_origin`, `max_model_requests` — is a measure, written under `measures`; a case that
still writes it under `assertions` has it read as a measure, never as a pass criterion.

### Assistants

An assistant ([ADR 0133](adr/0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md))
is a file of `kind: Assistant` listed under `contents.assistants` and validated by `assistant.schema.json`.
It wraps one of the plugin's agents and declares what the agent needs to converse with a person: the
configuration the installer sets, the audience, the tools it needs and the identity each is called under,
its memory spaces, the knowledge it offers, its background tasks, its surfaces and its policy.

```yaml
# assistants/investment-assistant.yaml (tests/fixtures/plugins/investment-assistant)
apiVersion: choregos/v1
kind: Assistant
metadata: { name: investment-assistant, display_name: Investment assistant, description: "…" }
agent: investment-analyst            # an agent of this plugin, runtime prompt or hosted
config_schema: { type: object, required: [house_name], properties: { house_name: { type: string } } }
audience: { personal_data: true }
tools:
  - { need: market_data, operations: [get_quote], access: read, identity: service, subject_resolver: true }
  - { need: orders, operations: [place_order], access: write, identity: user_consent, approval: console }
memory:
  - { name: instrument_notes, scope: user_subject, subject: { type: instrument, key: isin, resolver: none },
      kind: facts, writes: agent_proposes }
knowledge:
  - { name: investment_policy, seed: knowledge/investment-policy }
tasks:
  - { name: morning_brief, workflow: workflows/morning-brief.yaml, delegation: read_only,
      needs: [market_data], triggers: [{ kind: schedule, cron: "30 7 * * 1-5", user_timezone: true }] }
policy:
  conversation: { retention_days: 30 }
  data: { classification: sensitive, erasure: [by_user] }
```

`choregos plugin validate` refuses, naming the file and the JSON path of the field:

1. an `agent` that is not an agent of the plugin, or whose runtime is not `prompt` or `hosted`;
2. a `{{ config.<key> }}` of the agent's instructions that `config_schema` does not declare;
3. a connector of the agent's `mcp_servers` that no need declares, or a tool that is not an operation of
   its need (patterns included: an assistant's tools are a closed list; the agent calls them
   `<need>__<operation>`);
4. a write need without `approval`, or with `approval: none`; an `inline` approval under the `service`
   identity;
5. a `user_subject` memory without `subject`, or without a resolver (`resolver: none` for a public subject,
   a need marked `subject_resolver: true` otherwise);
6. `audience.personal_data: true` without `policy.conversation.retention_days` and an `erasure` mode;
7. a task that calls a write need, or that reads with the person's identity while it runs without them (a
   schedule, a poll) and has no `delegation: read_only`; a delegated task lists the needs it reads
   (`needs`), which the person sees when they agree;
8. a name (`need`, operations, `memory`, `knowledge`, `tasks`, prompts) that is not English `snake_case`
   (a double underscore separates a need from its operation and is refused in a name).

Names are unique within their list, a prompt's `{argument}` placeholders are its arguments, and every file
the assistant names ships with the plugin: each knowledge seed (a directory holding a `knowledge.yaml` in
the `kind: KnowledgeBase` shape, and its `corpus/`), each task workflow and each eval suite is a declared
file, packed and digested like a template's. Conversing with an assistant comes with the other stories of
stream S63 ([the plan](plan/PLAN-2026-11-10-assistants.md)).

#### Installing an assistant and binding its needs

Installing the plugin makes its assistants available; installing an assistant (S63-02) makes one of them an
object of the organisation, for an audience. The manifest is never copied: it is read again from the
installed plugin, so a plugin upgrade is seen at once.

```
POST  /api/v1/orgs/{org}/assistants      {"plugin": "investment-assistant", "assistant": "investment-assistant",
                                          "slug": "invest", "config": {"house_name": "Varga"},
                                          "owner_groups": ["investment-owners"], "audience_groups": ["grp-investors"]}
GET   /api/v1/orgs/{org}/assistants                         # those you manage
GET, PATCH, DELETE /api/v1/orgs/{org}/assistants/{slug}
GET   /api/v1/orgs/{org}/assistants/{slug}/readiness        # what is missing, and who may fix it
PUT   /api/v1/orgs/{org}/assistants/{slug}/bindings/{need}  {"connector": "news-feed"}
```

An installation is `incomplete` until its readiness has no item: each **required** need is bound to a
connector of the organisation that serves every operation of the need (as discovered, with the same
`access`) and can carry the need's identity mode, and each required key of `config_schema` has a value.
`service` is always supported; `user_exchange` and `user_consent` only by a connector whose config declares
them in `identity_modes`. A binding to a connector that cannot satisfy the need is a 422 naming why, and
nothing changes. An optional need may stay unbound or be switched off (`"enabled": false`). The status is
`suspended` while the plugin is uninstalled (installing it again resumes the assistant). Installing,
uninstalling and changing the owners are the organisation administrator's; configuring and binding are also
open to a member of an owner group (the identity provider's groups).

## Pack and index

```
choregos plugin validate hello/
choregos plugin pack hello/ -o hello-1.0.0.tar.gz
choregos plugin index plugins/ -o index.yaml --oci-repository ghcr.io/acme/choregos-plugins
choregos plugin index plugins/ -o index.yaml --git-url https://github.com/acme/agents \
    --git-commit <40-hex sha> --git-path plugins
```

The **content digest** (`sha256:…`) identifies a plugin. It is the sha256 of a canonical tar of its
declared files (nothing else is in the archive): entries sorted by path, regular files only, mode 0644, time 0, owner and group 0. The same
files always give the same digest, whatever their times or permissions on disk, and `pack` writes the
same bytes every time. An index (`plugin-index.schema.json`) records, for each version, its digest, its
`core` range, where to fetch it — an OCI reference, or a git repository pinned to a commit — and the
`agents` it ships (written by `choregos plugin index`): the workflow catalogue reads it to name the plugin
of an agent a workflow needs and the organisation lacks, without fetching any plugin
([ADR 0111](adr/0111-a-workflow-catalogue.md)). A
branch or a tag is not accepted in place of a commit.

## Installing into an organisation

An organisation administrator installs, upgrades and uninstalls (ADR 0074); every member lists:

```
GET    /api/v1/orgs/{org}/plugins
POST   /api/v1/orgs/{org}/plugins/install        {"source": "varga", "name": "hello", "version": "^1"}
POST   /api/v1/orgs/{org}/plugins/{name}/upgrade {"version": "1.1.0"}   # absent: the newest
DELETE /api/v1/orgs/{org}/plugins/{name}
GET    /api/v1/orgs/{org}/plugin-approvals
POST   /api/v1/orgs/{org}/plugin-approvals/{id}/approve
```

In the console: **Library → Plugins** (sources, what they offer, what is installed, the requests waiting
for a second administrator).

`source` is one the deployment declares ([Sources and trust](#sources-and-trust)), or `offline`; the
version is exact or a range, and the newest version inside it that this core admits is installed. Every
install and upgrade fetches through `fetch_plugin`, so every check of the table below applies; a refusal
is a 422 naming the check (`signature`, `digest`, `lock`…), a plugin or version the source does not
offer a 404.

**A plugin that brings a server, or a skill the gate rates high risk, waits for a second
administrator** (ADR 0050). Its install or upgrade
is checked in full, nothing is written, and the answer is `202 {"status": "pending_approval",
"approval_id", "reason"}`. Asking again answers the same request. Another administrator of the
organisation approves it with `POST …/plugin-approvals/{id}/approve` (the one who asked gets a 403); the
`reason` lists every cause: each server, and each high-risk skill with the findings that rate it so
(`script`, `executable`, `outbound_url`, `mcp_reference`, or any critical one);
the plugin is then fetched again at the recorded version and installed only if its content digest is
the one that was checked (a 409 otherwise: ask again). The install is recorded as the requester's.
`GET …/plugin-approvals` lists the requests, waiting and approved, to every member.

**Before anything is written**, each agent passes the full `AgentCreate` model, its instructions must
render and its network must be one the organisation allows; each skill passes the skill library's
validator; each eval suite is played on its agent's instructions and must pass; a procedure id the
platform or another plugin already has is a conflict; a plugin named in `requires.plugins` must be
installed. Any refusal leaves the organisation unchanged.

**What it installs:**

| Item | Where it goes | Its name in the organisation |
| :-- | :-- | :-- |
| agents | the agent registry, version 1, scope `platform`, author `plugin:<plugin>/<slug>@<catalogue version>` | its slug |
| skills | the skill library, version 1, author `plugin:<plugin>/<skill>@<plugin version>` | its name |
| templates | kept with their files | `plugin:<plugin>/<template>`, usable as a project's `template_ref` |
| evals | kept; played at every install and upgrade | `<agent>/<file>` |
| matrix cases | kept, never played at install; the live eval matrix reads them | `plugin:<plugin>/<file stem>` |
| procedures | kept; `GET /procedures` lists them for the organisation's members | their id |
| servers | run in the plugins namespace and governed as an organisation `mcp` connector ([Servers](#servers)) | the connector's name is the server's |

An agent or skill the organisation already has under that name, and that the plugin did not install,
wins: the plugin leaves it alone.

The files are kept in the database (`org_plugin_files`), keyed by the plugin's content digest and under
the organisation's row-level security, so a private plugin's text is not readable from another
organisation and an install that fails writes no file. The object store would offer neither.

**Upgrading** publishes the next version of an agent or skill only when its text changed. One the
organisation changed since (a version the plugin did not write) is **offered** the update in the answer
(`offered`) and never overwritten. Project pins do not move.

**Uninstalling** deletes what the plugin installed and nobody changed or pins. What an organisation
changed, what a project pins and a skill a kept agent still carries stay, as the organisation's own
(scope `org`). The answer lists what was removed and what was kept
(`{"name", "version", "removed": {"agents", "skills"}, "kept": {"agents", "skills"}}`), as does the
`plugin.uninstall` audit entry; a request still waiting to upgrade it is dropped. A plugin another
installed plugin requires cannot be uninstalled (409).

## Servers

A plugin brings code only as an MCP server image (ADR 0096). Installing the plugin (after the second
approval) runs each of its servers and governs it:

- **an organisation connector named after the server.** `servers/servicenow.yaml` with
  `name: servicenow` becomes the `mcp` connector `servicenow`, so an agent names it in `mcp_servers`
  (`{connector: servicenow, tools: [...]}`) and its tools are **`<server>__<operation>`**
  (`servicenow__search_records`). An organisation that already has a connector of that name gets a 409
  when it asks for the install: rename or delete it first. The connector goes with the plugin: the
  connector route refuses to delete it (409), uninstalling removes it, its operations and its secrets.
- **one operation per declared operation, at its floor.** A `read` is allowed, a `write` needs an
  approval (a governed action, ADR 0035); the administrator may change the policy, the groups and the
  price as for any connector, but an operation marked `human_approval: true` never goes below
  `approval` — the route refuses it and the broker re-applies the floor whatever a row says. A tool the
  server serves that `operations` does not list is refused: discovery does not add it, the broker does
  not offer it, a governed action does not play it. Discovery learns a declared tool's input schema; a
  later change of that schema closes it, as for any MCP server.
- **compensation and platform arguments.** `compensation: {operation, arguments}` is attached to the
  governed action of the write, its `arguments` rendered from the write's own result
  (`{sys_id: "{{ result.sys_id }}"}`), and played if a later step of the action fails.
  `platform_arguments: [action_key]` makes the platform fill an idempotency key stable for one action
  and call (`work_item_key`: the action's work item); whatever the agent wrote under those names is
  dropped.

### What the server must do

- **Refuse every call without the platform's token.** The platform generates a token per server and
  organisation, seals it in the organisation's secret store (ADR 0058), gives it to the pod as
  `CHOREGOS_SERVER_TOKEN`, and presents it as `Authorization: Bearer <token>` on every call to
  `paths.mcp`. A server that answers without it lets anything in the cluster that reaches it act with
  its credentials.
- **Answer MCP over HTTP** (`protocol: mcp-http`) at `paths.mcp`, and `200` at `paths.health`
  (readiness and liveness probes).
- **Run as a non-root user with a read-only root filesystem**; `/tmp` is a small writable `emptyDir`.
  The pod has no ServiceAccount token, drops every capability and runs under the deployment's
  RuntimeClass (gVisor when it sets one).
- **Read its configuration from the environment**: each `config_schema` property and each
  `secret_fields` entry reaches it as the variable named by the property's `x-env`, else its name in
  upper case (`base_url` → `BASE_URL`). A list of scalars is joined with commas, an object is JSON.
  `CHOREGOS_*` names are the platform's.

### Configuring a server

The connector's configuration **is** the server's: `PATCH /orgs/{org}/connectors/{server}` with
`config` (checked against `config_schema`, required fields aside) and `secret_values` for the
`secret_fields` (sealed, never shown again; `PUT …/secrets/{field}` works too). Every change is applied
to the server's Secret, and the digest of the configuration on the pod template rolls the Deployment.
Until the configuration satisfies the whole schema the Deployment has no replica and the connector
says `unconfigured`; then `deployed`. The connector routes answer the server's plugin, version, `config_schema` and
`secret_fields` under `server` (null for any other connector), so the console draws the server's own form.

### Network

The server's pods admit only the API's pods (`plugins.servers` puts the API's namespace in the policy).
Their egress is the hosts their configuration names (`egress.from_fields`, or the fixed `egress.hosts`):
each is checked against the organisation's network bounds when it is typed (a denied host is a 422), and
the server's NetworkPolicy lets through DNS and public addresses on those hosts' ports (never the
cluster's, the node's or the metadata endpoint's addresses). Kubernetes cannot name a host: with
`plugins.servers.egress: cilium` a `CiliumNetworkPolicy` narrows egress to those exact names.

### On its fake, for the eval matrix

A server may declare how the platform runs it on its fake for the live eval matrix (#1372):

```yaml
fake:
  env: {SERVICENOW_FAKE: "1"}   # what switches it to its seeded fake instance
  scenes: [default]             # the scenes that fake serves (`default` when absent)
```

Its variables are not configuration: none may be `CHOREGOS_*` or one a `config_schema` property or a
`secret_fields` entry reaches, so no administrator switches a server to its fake. Where the deployment
serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES=1`), and only there, the platform runs beside the server
its **eval instance**: the same image, the fake's variables and the platform's token, nothing of the
configuration, no egress, one replica (`<server>-fake-<org digest>`); the next reconcile deletes it once
the switch is off. A connector reaches the eval instance only when the live matrix's seeding bound it
(`fake_scene: <scene>` in its configuration), which every declaration and edit refuses (422); without the
switch the field is ignored and the connector is the server's own instance. The seeding binds only a
server's connector nobody configured, available to the selected projects and turned on for its eval
projects.

### Images

A server's image is pinned by digest (the contract), comes from a registry prefix the operator lists in
`plugins.servers.registries`, and carries a trusted signature when `images.hostedCosign` is `enforce`
(`warn` logs `plugin.server.signature_unverified`). A deployment without `plugins.servers.enabled`
refuses a plugin that brings a server. In the cluster, the Kyverno policy
`choregos-plugin-server-images` (`infra/policies/plugin-servers.yaml`) refuses a pod of the plugins
namespace whose image is not pinned or not from the allowed prefix.

## The tracker profile

A server that declares `implements: [tracker]` can be a project's tracker (plan S43 Decision 3, S43-07):
the core's `tracker/plugin` adapter maps `TrackerAdapter` onto eight tools the server serves. Their
arguments and answers are normative: `packages/contracts/schemas/plugin-tracker-profile.schema.json`
(`$defs/<tool>_input`, `$defs/<tool>_output`); the adapter checks every answer against it and refuses
one that does not match. Each answer is a JSON object (MCP `structuredContent`, or a text part holding
it); a refusal is an MCP error (`isError: true`) whose text starts with `refused:`. The first server
written against it is ServiceNow's (`choregos-agents`, `plugins/servicenow/server/TRACKER_PROFILE.md`).

| `TrackerAdapter` | Tool | Declared access |
|:--|:--|:--|
| `fetch_item(key)` | `tracker_fetch_item {key}` → the core's `WorkItemData` | `read` |
| `create_item(data)` | `tracker_create_item {title, body, labels, assignees, fields, action_key}` → `{key, created}` | `write` |
| `set_state(key, mapping)` | `tracker_set_state {key, status, label, remove_labels}` → `{key, changed, state?, ignored?}` | `write` |
| `comment(key, markdown)` | `tracker_comment {key, markdown, action_key}` → `{key, id, written}` | `write` |
| `upsert_status_comment(key, markdown, marker)` | `tracker_upsert_status {key, markdown, marker}` → `{key, written}` | `write` |
| `close_item(key)` | `tracker_close {key, close_notes, close_code}` → `{key, closed, state?}` | `write` |
| `list_candidates(project)` | `tracker_list_candidates {limit}` → `{keys, reason?}` | `read` |
| webhook | `tracker_parse_event {headers, body}` → `{verified, events: [{kind, item_key, delivery_id}]}` | `read` |

`link` and `set_fields` are not in the profile: the adapter skips them (a field write is an agent's
governed operation of the server). The loader refuses a tracker server that does not declare one of the
eight tools in `operations`, or declares a write as a `read` (that would allow it to every agent without
approval).

- **Idempotent writes.** `action_key` is derived by the platform from the call (`platform:<hash>` of
  the tool, the key and the text): a Temporal retry replays the same key and the server answers the
  first write (`created: false`, `written: false`). The same comment twice on an item is written once.
  `tracker_upsert_status` adds a note only when the latest one with that `marker` differs (journals that
  cannot be edited); Choregos's markers (`<!-- choregos:status -->`) reach the server as their name
  (`choregos:status`).
- **Platform calls, audited, not governed.** The adapter calls the server through the platform's client
  of it (the Service URL the platform wrote, the server's bearer token, no redirect), not through an
  agent's tool selection nor a governed action — as the platform's writes to Jira or GitHub. The workflow
  already decided each write (a mirrored state, a status comment, a delivered item closed after its
  review stage); an approval per write would put a person in front of every transition. Each write is
  an audit entry `tracker.plugin.<tool>` of the organisation (written in its own transaction: the write
  happened in the server's system whatever happens next). An agent calling the same tool goes through
  the broker under the declared policy, like any operation.
- **A project's tracker.** A template that says `requires.connectors.tracker: plugin:<plugin>/<server>`
  gives a project born from it the tracker connector `{type: plugin, config: {plugin, server}}`; the
  creation is a 422 while the plugin is not installed in the organisation or the server is not
  `deployed` (configure it first). `PUT /projects/{id}/connectors/tracker` with `type: plugin` checks
  the same and accepts `plugin` and `server` only: where the server answers and its token are read from
  the organisation's connector each time the adapter is built, never typed nor stored on the project.
- **Webhooks.** A procedure gives `{{plugin_webhook_url:<plugin>/<server>}}`; the system posts its
  deliveries there. `POST /webhooks/plugins/{org}/{plugin}/{server}` forwards the headers (without
  `Authorization`, `Cookie`) and the body to the server's `tracker_parse_event` over `/mcp` — the
  server's only door the platform authenticates (ADR 0096 §6), so a server's own `webhook.path` is not
  called by the core. The URL names the organisation, so a delivery reaches only that organisation's
  server (401 if the server does not recognise it, 404 if the organisation does not run that server as a
  tracker with a webhook). Each event is de-duplicated on its `delivery_id`, then its
  item is **re-read** with `tracker_fetch_item`: nothing of the delivery but the key is trusted. The
  event goes to the organisation's project that holds the item, or its only project on that tracker.
- **Polling.** `list_candidates` is the profile's `tracker_list_candidates`, read by the tracker
  reconciliation like any external tracker's, for systems that send no events.
- **Fakes.** Under `CHOREGOS_FAKES=1` a plugin tracker is built as itself and talks to
  `FakePluginTrackerServer` (`choregos_adapters.fakes.plugin_tracker`), an in-memory server of the
  profile with the same replay rules.

## Default plugins

A deployment lists plugins every organisation gets (`defaultPlugins`, the chart's
`global.plugins.defaultPlugins`, see [deployment](deployment.md#plugins)). The API installs them at
start-up in every organisation and in each organisation created later, idempotently, through the same
checks; they are listed with `default: true` and `installed_by: null`. One the gate sends to a second
administrator (a server, a high-risk skill) is not installed by the platform: it waits in
`GET …/plugin-approvals` (`default: true`, no `requested_by`) until an administrator of the organisation
approves it — unless the entry says `preapproved: true`.

- **`preapproved: true`** is the operator's approval, given in the deployment's configuration and so
  through that configuration's own review. The reconcile installs the plugin and records
  `plugin.default.preapproved` in the audit trail, with the gate's reasons and `approved_by: deployment
  configuration`. The API takes no such flag: a person's install always waits for the second
  administrator, and without the flag a default plugin waits too.
- **Offline first.** A default of a declared source is taken from the offline directory when it holds a
  version inside the requested range — no network at start-up, the content digest the source published
  — and recorded under its declared source, which serves its upgrades.

### The built-in agents

The platform's own agents left the core (ADR 0093 §6, S43-09) for the open-source repository
[`VargaFoundation/choregos-agents`](https://github.com/VargaFoundation/choregos-agents):

| Plugin | What it ships |
|---|---|
| `software-delivery` | the 19 delivery agents (triage to the production go/no-go) and the skill `madr-4` |
| `netops` | `netops`, through the organisation's pfSense, Cilium and nginx connectors |
| `authoring` | `agent-drafter` (the agent draft of the console) and `knowledge-distiller` |
| `mcp-clients` | the six MCP clients of the Integrations page (ChatGPT, Claude, Copilot, Cursor) |
| `requests` | `brief-writer` (role `brief`) and `assistant` (role `deliver`), the agents of `default-request` (ADR 0110) |

They are published, signed, to `ghcr.io/vargafoundation/choregos-agents/<plugin>:<version>` with the
index `oci://ghcr.io/vargafoundation/choregos-agents/index:latest`. The core ships their archives
offline in `plugins/offline/`, pinned by `plugins.lock` (`make plugins-lock` refreshes them, with the
network; `make plugins-check` checks them without), and the API image copies them to
`/opt/choregos/plugins`. The chart installs all five by default; the first four pre-approved (`madr-4` links to
adr.github.io and is rated high risk), `requests` needing no second administrator.

The **agent catalogue** (`/orgs/{org}/agent-catalogue`, the console's catalogue page) is a view over
plugins: the offline directory's, each replaced by the version the organisation installed. Installing an
entry from it installs that one agent (and its skills) with its plugin's provenance,
`plugin:<plugin>/<slug>@<version>`, as the plugin itself would; a template's `catalogue:<slug>` and a
workflow that names an agent resolve through the same view. Organisations that installed catalogue
agents before S43-09 had their provenance rewritten by a migration, and an `org_plugins` row recorded
for each plugin they held agents of, so upgrades keep reaching them.

The full reference of the commands is in [the CLI reference](cli.md#choregos-plugin).

## Sources and trust

A deployment declares where plugins come from in the file `CHOREGOS_PLUGINS_CONFIG` names (the chart
writes it from its `global.plugins` values, [deployment](deployment.md#plugins)):

```yaml
sources:
  - name: varga
    index: oci://ghcr.io/vargafoundation/choregos-agents/index:latest   # or https://…/index.yaml
    trust:                      # a keyless signer, regular expressions matched whole…
      identity: https://github\.com/VargaFoundation/choregos-agents/\.github/workflows/publish\.yml@refs/heads/main
      issuer: https://token\.actions\.githubusercontent\.com
      # key: /etc/choregos/plugins/varga.pub   # …or a public key cosign reads
      # tlog: false             # only for a key whose signatures never reached Rekor
    egress: [registry.acme.example]   # hosts beyond the index's own
defaultPlugins:
  - {source: varga, name: servicenow, version: "^0.1"}
  - {source: varga, name: software-delivery, version: "^1.0", preapproved: true}
offline: /opt/choregos/plugins  # or CHOREGOS_PLUGINS_OFFLINE; both, and different: the API does not start
```

Every source has a trust policy; there is no unsigned source. `GET /orgs/{org}/plugin-sources` lists
the sources, the offline directory as the source `offline`, each with what its index offers and
whether this core is inside each version's `core` range. A source whose index cannot be read is listed
with the reason.

### What a fetch checks

A fetch (`choregos_api.services.plugin_sources.fetch_plugin`, which install and upgrade call) refuses
at the first failed check, naming it:

| check | refuses |
|---|---|
| `version` / `not_found` | a plugin the index does not offer, or no version inside the requested range |
| `core` | every matching version requires another core (the index's `core`, then the manifest's) |
| `signature` | an OCI release whose manifest carries no signature by the source's identity and issuer (or key); an `oci://` index that is not signed so; a git release from an unsigned `https://` index |
| `git_ref` | a git release that is not pinned to a 40-hex commit (the index schema refuses it too) |
| `digest` | fetched files whose canonical-tar digest differs from the index's |
| `lock` | an offline plugin that differs from `plugins.lock`, or is not in it |
| `content` | a file the loader refuses, or a manifest whose name or version is not the index's |
| `fetch` | a host that did not answer, or that the egress policy refuses |

- **OCI.** A plugin is an artifact with one layer of type `application/vnd.choregos.plugin.v1.tar+gzip`
  (the `pack` output); an `oci://` index is an artifact with one layer of type
  `application/vnd.choregos.plugin-index.v1+yaml`. Cosign verifies the manifest digest, as for hosted
  images (ADR 0080 §8); the layer's bytes are checked against the manifest, then the content digest
  against the index. Publish with `oras push <ref> plugin.tar.gz:application/vnd.choregos.plugin.v1.tar+gzip`
  then `cosign sign <ref>@<digest>`.
- **Git.** A git commit carries no cosign signature, so a git release is trusted through its index: it
  is fetched only from an `oci://` index that verified. Only `github.com` and `gitlab.com` are fetched
  (the skill import's tarball fetcher).
- **Offline.** Plugins as directories or `.tar.gz` directly in the directory, trusted by their presence
  in the image. With a `plugins.lock` (`plugins: {name: {version, digest}}`, other keys ignored), each
  must be in it with that digest, when the index is read and again when the plugin is fetched.

### Egress

Sources are the operator's, not an organisation's, so their fetches are not checked against an
organisation's allowlist. They go through the guarded transport (ADR 0066) under a policy that opens the
index's host, the hosts `egress` lists, the blob storage of `ghcr.io` and Docker Hub, and GitHub or
GitLab for a git release, all still bounded by the deployment's ceiling (`CHOREGOS_EGRESS_CEILING`) and
mode. An index that names an artifact on another host reaches it only when that host is in `egress`.

### The supply-chain gate

Once fetched, `assess_plugin` applies the rest of ADR 0050's gate. Each of its skills is assessed like
an imported skill, and a plugin that brings a server or a `high` skill needs a second administrator's
approval (an install or upgrade answers 202 until it is given, see
[Installing into an organisation](#installing-into-an-organisation)). That approval is the skill's
review: once given, the plugin's skills are installed approved, a high-risk one included, with their
assessment recorded.

