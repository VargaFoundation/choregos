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

- **Paths stay inside the plugin.** A path cannot be absolute or hold a `.` or `..` segment, and the
  tree cannot hold a symbolic link, even one pointing inside it. These are the template rules, shared
  in `choregos_core.paths`.
- **The manifest** matches `plugin.schema.json`. A kind other than `Plugin`, a version that is not
  semver, or an unknown key is refused.
- **Agents** have the shape of a built-in catalogue entry (ADR 0040): `catalogue.version`, `role` and
  `summary`; `agent.slug`, `display_name` and `spec`. The full `AgentCreate` validation runs when the
  plugin is installed.
- **Skills** are directories that hold a `SKILL.md`.
- **Templates** have a `manifest.yaml` that matches `template.schema.json`.
- **Evals** name an agent of the plugin.
- **Procedures** go through the same parser as the core's (ADR 0067): front matter, fixed sections,
  links listed in `vendor_docs`, no secret-shaped string.
- **Servers** match `plugin-server.schema.json`:
  - the image is pinned by `@sha256:`;
  - every operation states its `access` (`read` or `write`);
  - a read has no `compensation`, and a compensation names an operation of the same server;
  - operation names are unique;
  - an egress `from_fields` entry is a field of `config_schema`.

  An operation's `human_approval` is a floor: an organisation may raise it, never lower it. A tool the
  server serves but does not list is refused.

## Pack and index

```
choregos plugin validate hello/
choregos plugin pack hello/ -o hello-1.0.0.tar.gz
choregos plugin index plugins/ -o index.yaml --oci-repository ghcr.io/acme/choregos-plugins
choregos plugin index plugins/ -o index.yaml --git-url https://github.com/acme/agents \
    --git-commit <40-hex sha> --git-path plugins
```

The **content digest** (`sha256:…`) identifies a plugin. It is the sha256 of a canonical tar of the
directory: entries sorted by path, regular files only, mode 0644, time 0, owner and group 0. The same
files always give the same digest, whatever their times or permissions on disk, and `pack` writes the
same bytes every time. An index (`plugin-index.schema.json`) records, for each version, its digest, its
`core` range, and where to fetch it: an OCI reference, or a git repository pinned to a commit. A
branch or a tag is not accepted in place of a commit.

## Installing into an organisation

An organisation administrator installs, upgrades and uninstalls (ADR 0074); every member lists:

```
GET    /api/v1/orgs/{org}/plugins
POST   /api/v1/orgs/{org}/plugins/install        {"source": "offline", "name": "hello", "version": "1.0.0"}
POST   /api/v1/orgs/{org}/plugins/{name}/upgrade {"version": "1.1.0"}   # absent: the newest
DELETE /api/v1/orgs/{org}/plugins/{name}
```

Until the sources of S43-03 are wired in, the one source is `offline`: the directory named by
`CHOREGOS_PLUGINS_OFFLINE`, holding plugin directories (`<name>/` or `<name>/<version>/`), installed at
an exact version.

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
| servers | recorded, not run (S43-06): the plugin's status is `servers_pending` | their name |

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
(scope `org`); the `plugin.uninstall` audit entry lists them under `kept`. A plugin another installed
plugin requires cannot be uninstalled (409).

The full reference of the commands is in [the CLI reference](cli.md#choregos-plugin).

## Sources and trust

A deployment declares where plugins come from in the file `CHOREGOS_PLUGINS_CONFIG` names (the chart
writes it, S43-05):

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
offline: /opt/choregos/plugins  # CHOREGOS_PLUGINS_OFFLINE wins over it
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

Once fetched, `assess_plugin` applies the rest of ADR 0050's gate: a plugin that brings a server needs
a second person's approval (an install answers 202 until it is given), and each of its skills is
assessed like an imported skill (a high-risk one is installed waiting for review).

