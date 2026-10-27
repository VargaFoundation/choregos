# Writing a plugin

A plugin packages agents and what they need, in one format the platform understands
([ADR 0093](adr/0093-an-agent-ships-as-a-plugin.md)). This page covers writing one and checking it
locally with the CLI, and what installing one into an organisation does. Sources and signatures (S43-03)
and the plugins a deployment installs by default (S43-05) are later stories.

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
