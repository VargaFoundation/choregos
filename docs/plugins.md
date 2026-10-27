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
├── procedures/connector.mcp.hello.md    # a procedure (ADR 0067 front matter)
└── servers/hello.yaml               # an MCP server the platform runs (plugin-server.schema.json)
```

A complete example lives in `tests/fixtures/plugins/hello/`.

**A plugin is what its manifest declares**: `plugin.yaml`, a `README.md` and a `LICENSE` at its root when
present, and exactly the paths `contents` names — each file, every file under a skill or template
directory, every file a procedure pattern matches. Anything else in the directory (a server's source, its
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
  server must be one of the same plugin and declare `implements: [tracker]`. (The adapter that makes such
  a server a project's tracker is S43-07: until then a project cannot be created from such a template.)
- **Evals** match `plugin-evals.schema.json` and name an agent of the plugin (see [Evals](#evals)).
- **Procedures** go through the same parser as the core's (ADR 0067): front matter, fixed sections,
  links listed in `vendor_docs`, no secret-shaped string. A procedure gives the address of its server's
  webhook with `{{plugin_webhook_url:<plugin>/<server>}}`, filled with
  `<api>/api/v1/webhooks/plugins/<plugin>/<server>`; it may name only a server of its own plugin that
  declares a `webhook`.
- **Servers** match `plugin-server.schema.json`:
  - the image is pinned by `@sha256:`;
  - every operation states its `access` (`read` or `write`);
  - a read has no `compensation`, and a compensation names an operation of the same server;
  - `paths` (`mcp`, `health`; defaults `/mcp` and `/healthz`) name where the server answers;
  - `platform_arguments` (`action_key`, `work_item_key`) are arguments the platform fills, never the
    agent;
  - operation names are unique;
  - an egress `from_fields` entry is a field of `config_schema`.

  An operation's `human_approval` is a floor: an organisation may raise it, never lower it. A tool the
  server serves but does not list is refused.

### Evals

An eval file (`plugin-evals.schema.json`) has the shape of the core catalogue's own cases
(`choregos_core/catalogue_d_agents/eval_cases/`), plus the API's `AgentEvalCase` list:

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
  - name: live-greeting
    live: true                     # a capped trial on a real model (S40-02d)
    expect_status: done
    max_cost_usd: 0.25
    must_contain: [Hello]
```

An unknown key is refused, and a suite holds `must_contain`, `cases` or both. The installer plays level 1
and every case that is not `live`; a failure refuses the plugin. A `live` case is validated, not run at
install.

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
`core` range, and where to fetch it: an OCI reference, or a git repository pinned to a commit. A
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

A plugin brings code only as an MCP server image (ADR 0094). Installing the plugin (after the second
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
says `unconfigured`; then `deployed`.

### Network

The server's pods admit only the API's pods (`plugins.servers` puts the API's namespace in the policy).
Their egress is the hosts their configuration names (`egress.from_fields`, or the fixed `egress.hosts`):
each is checked against the organisation's network bounds when it is typed (a denied host is a 422), and
the server's NetworkPolicy lets through DNS and public addresses on those hosts' ports (never the
cluster's, the node's or the metadata endpoint's addresses). Kubernetes cannot name a host: with
`plugins.servers.egress: cilium` a `CiliumNetworkPolicy` narrows egress to those exact names.

### Images

A server's image is pinned by digest (the contract), comes from a registry prefix the operator lists in
`plugins.servers.registries`, and carries a trusted signature when `images.hostedCosign` is `enforce`
(`warn` logs `plugin.server.signature_unverified`). A deployment without `plugins.servers.enabled`
refuses a plugin that brings a server. In the cluster, the Kyverno policy
`choregos-plugin-server-images` (`infra/policies/plugin-servers.yaml`) refuses a pod of the plugins
namespace whose image is not pinned or not from the allowed prefix.

## Default plugins

A deployment lists plugins every organisation gets (`defaultPlugins`, the chart's
`global.plugins.defaultPlugins`, see [deployment](deployment.md#plugins)). The API installs them at
start-up in every organisation and in each organisation created later, idempotently, through the same
checks; they are listed with `default: true` and `installed_by: null`. One that brings a server is not
installed by the platform: it waits in `GET …/plugin-approvals` (`default: true`, no `requested_by`)
until an administrator of the organisation approves it.

The full reference of the commands is in [the CLI reference](cli.md#choregos-plugin).

## Sources and trust

A deployment declares where plugins come from in the file `CHOREGOS_PLUGINS_CONFIG` names (the chart
writes it from its `global.plugins` values, [deployment](deployment.md#plugins)):

```yaml
sources:
  - name: varga
    index: oci://ghcr.io/vargafoundation/choregos-agents/index:stable   # or https://…/index.yaml
    trust:                      # a keyless signer, regular expressions matched whole…
      identity: https://github.com/VargaFoundation/choregos-agents/.*
      issuer: https://token.actions.githubusercontent.com
      # key: /etc/choregos/plugins/varga.pub   # …or a public key cosign reads
      # tlog: false             # only for a key whose signatures never reached Rekor
    egress: [registry.acme.example]   # hosts beyond the index's own
defaultPlugins:
  - {source: varga, name: servicenow, version: "^0.1"}
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
  in the image. With a `plugins.lock` (`plugins: {name: {version, digest}}`), each must be in it with
  that digest, when the index is read and again when the plugin is fetched.

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

