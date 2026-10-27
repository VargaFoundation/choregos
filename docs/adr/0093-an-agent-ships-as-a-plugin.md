# 0093 — An agent ships as a plugin: one format, signed sources, default plugins per deployment

- **Status**: accepted, 2026-10-27, by the product owner's requests of that day (plan
  `docs/plan/PLAN-2026-10-27-servicenow.md`, stream S43) — contract only (S43-01); implemented by
  S43-02 onwards
- **Concerns**: `packages/contracts/schemas/plugin.schema.json`, `plugin-server.schema.json`,
  `plugin-index.schema.json`; the routes `GET /orgs/{org}/plugins`, `GET /orgs/{org}/plugin-sources`,
  `POST /orgs/{org}/plugins/install`, `POST /orgs/{org}/plugins/{name}/upgrade`,
  `DELETE /orgs/{org}/plugins/{name}`, `POST /webhooks/plugins/{plugin}/{server}`; `choregos_core.plugins`,
  the CLI `choregos plugin`, the chart's `plugins` values; amends
  [ADR 0040](0040-a-built-in-agent-catalogue.md) (the catalogue becomes plugins),
  [ADR 0065](0065-catalogues-of-mcp-servers-and-skills.md) and
  [ADR 0067](0067-a-procedure-per-connector-and-agent-type.md) (procedures come with their plugin);
  builds on [ADR 0050](0050-skill-supply-chain-gate.md) (the supply-chain gate) and
  [ADR 0074](0074-catalogue-rights.md) (who installs)

## Context

On 2026-10-27 the product owner asked for a ServiceNow agent, then for it to be "a complete plugin that
embeds its agent, in a format the platform understands — the same for every future agent", published
from an open-source repository of agents, with each deployment listing the plugins it installs by
default and plugins from other repositories possible.

Surveyed on `main` at `7919257b`:

- **No package format exists.** A template (`templates/<name>/manifest.yaml`) ships agents, skills,
  workflows and extensions, but only from the image's `templates/` directory; a template published through
  `POST /templates` carries no file.
- **An agent cannot come from outside the image.** The catalogue (ADR 0040) is the package directory
  `choregos_core/catalogue_d_agents/agents/*.yaml`, 23 entries, with no source and no override.
- **Only skills install from outside**: a zip, or a GitHub/GitLab tarball pinned to a commit, behind a
  per-organisation allowlist and the supply-chain gate (ADR 0050).
- **Connector code needs a derived image.** `choregos.plugins` wheels load only from an image built on
  top of the core's. An organisation `mcp` connector, on the other hand, reaches a server by URL and the
  broker governs each of its tools (`ConnectorOperation`, approval floors, governed actions).

The platform therefore already governs everything a plugin would bring. What is missing is the package,
its sources and their trust, the installer, and a way to run a plugin's server without building an image.

## Decision

1. **One format, `kind: Plugin`.** A plugin is a directory with `plugin.yaml` at its root
   (`apiVersion: choregos/v1`), validated by `plugin.schema.json`: `metadata` (`name` in `[a-z0-9-]`,
   unique within a source; `version` in semver, immutable once published; `publisher`, `license`,
   `description`, optional `homepage`; `core`, the range of core versions it was validated against),
   `requires` (other plugins by name and range, project connectors and organisation connectors as in a
   template) and `contents`: `agents` (today's catalogue entry shape, `catalogue:` + `agent:`, so the
   built-in agents move without being rewritten), `skills` (SKILL.md directories), `templates` (today's
   template shape, **with their files**), `evals`, `procedures` (ADR 0067 front matter; the only list
   that may use `*`) and `servers`.
   Paths are relative, cannot be absolute, cannot hold a `.` or `..` segment, and a symbolic link is
   refused — the rules templates already follow. The Python entry-point group `choregos.plugins` keeps
   its role; the documentation calls those **extensions** from now on, so that "plugin" means one thing
   to users.
2. **A plugin brings code only as an MCP server image** (`plugin-server.schema.json`): an image pinned
   by `@sha256:` digest (a tag alone is refused), a port, protocol `mcp-http`, the configuration schema an
   administrator fills, its secret fields (sealed, ADR 0058), its egress (hosts read from configuration
   fields, or fixed hosts), and the **operations** it may serve, each with a required `access`
   (`read`/`write`, nothing is assumed read), an optional `human_approval` floor that an organisation may
   raise and never lower, and for a write an optional `compensation`. A tool not listed is refused.
   `implements: [tracker]` and `webhook.path` let a server act as a tracker. How the platform runs and
   governs these servers is ADR 0094's (S43-06).
3. **Distribution.** A published plugin is an OCI artifact
   (`application/vnd.choregos.plugin.v1.tar+gzip`) signed with cosign, or a git reference pinned to a
   40-hex commit. Its identity is the **content digest**: the sha256 of a canonical tar of the directory
   (entries sorted, modification time 0, owner and group 0, no symbolic link), which `choregos plugin
   pack` produces and every fetch is checked against.
4. **Sources and trust.** A source is an index (`plugin-index.schema.json`: name → versions →
   `{digest, core, oci | git}`) and a trust policy (a keyless cosign identity and issuer, or a public
   key). Anyone can publish a source from their own repository. Every install and upgrade passes the
   supply-chain gate of ADR 0050: digest checked, signature verified against the source's policy, every
   file validated, and a second person's approval for a plugin that brings a server (the install answers
   202 until it is given). Rights per ADR 0074.
5. **What a deployment installs by default.** The chart's `plugins.sources`, `plugins.defaultPlugins`
   (source, name, version range) and `plugins.offline` (a directory baked into the image for air-gapped
   installs, demos and CI). The API reconciles at start-up, idempotently: each default plugin is installed
   in every organisation; running the reconcile twice changes nothing. Upgrades keep ADR 0040's rules: an
   agent an organisation changed is offered the update, never overwritten; project pins do not move.
6. **The built-in agents leave the core.** The 23 catalogue agents move to the open-source repository
   `VargaFoundation/choregos-agents`, grouped in domain plugins. The core keeps the loader and loses the
   YAML; its default values list those plugins, and the image bakes a lock snapshot into `offline`, so
   `make demo`, `CHOREGOS_FAKES=1` and CI stay offline. Provenance `catalogue:<slug>@<v>` becomes
   `plugin:<plugin>/<slug>@<v>` by a data migration, so organisations keep receiving upgrades.

## Amendments

- **ADR 0040**: the catalogue is no longer a directory of the core but the agents of installed plugins;
  its rules on installing, never rewriting and offering updates apply unchanged to plugin agents.
- **ADR 0065**: a catalogue entry may come from a plugin; the plugin's source and digest are its
  recorded source.
- **ADR 0067**: a procedure may come with its plugin (`contents.procedures`), validated by the same
  front-matter model and checks as a core procedure; it leaves when the plugin is uninstalled.

## Consequences

- A third party publishes agents, skills, templates and procedures without forking the core or building
  an image; code still runs only as a governed, out-of-process server.
- The contract (S43-01) lands before any route is served: the six operations stay in `CONTRACT_ONLY` of
  `apps/api/tests/test_contract_conformance.py` until S43-03, S43-04 and S43-07 serve them.
- The word "plugin" changes meaning in the documentation: what was called a plugin (a Python
  wheel loaded through `choregos.plugins`) is an extension.
- Not decided here: how plugin servers run (ADR 0094), the console's catalogue pages (S43-08).

## Precisions (S43-05, 2026-10-27)

- **The second approval** is a request kept in `org_plugin_approvals` at the exact version and content
  digest that was checked; `GET /orgs/{org}/plugin-approvals` lists them and
  `POST /orgs/{org}/plugin-approvals/{id}/approve` gives it. The requester cannot approve; the plugin is
  fetched again and installed only if the digest is unchanged. A default plugin is requested by the
  platform (no requester): any administrator of the organisation approves it, and the platform never
  does.
- **Uninstall answers 200** with what was removed and what was kept, instead of a 204 without a body.
- **The reconcile** never upgrades: an organisation that has a default plugin, at any version, is left
  alone. It runs on every replica, one organisation at a time under an advisory lock, and when an
  organisation is created.

## Precisions (S43-05b, 2026-10-27)

Found by the first real plugin (`servicenow`, choregos-agents PR #2):

- **A plugin is what its manifest declares.** `validate`, the content digest and `pack` cover
  `plugin.yaml`, a root `README.md` and `LICENSE` when present, and exactly the paths of `contents`
  (globs expanded); anything else in the directory — a server's source, a `.venv` — is ignored. The
  symbolic-link rule of §1 applies to declared paths and what is under a declared directory. The digest
  stays reproducible, and a git release yields the same digest as the packed archive.
- **A high-risk skill needs the second approval** (ADR 0050), like a server; the request's `reason`
  lists every cause. Approved, the skill is installed approved: the second approval is its review.
- **Evals have a format**, `plugin-evals.schema.json`: level 1 is the core catalogue's case shape, and
  `cases` the API's `AgentEvalCase`. The installer plays level 1 and the deterministic cases; a `live`
  case is validated, not run at install.
- **A procedure names its plugin's webhook** with `{{plugin_webhook_url:<plugin>/<server>}}` (ADR 0067),
  only for a server of its own plugin that declares a `webhook`.
- **A template's tracker may be a plugin's server**: `requires.connectors.tracker:
  plugin:<plugin>/<server>` (`template.schema.json`), checked at validation against a server of the same
  plugin that declares `implements: [tracker]`. The adapter that runs it is S43-07.
