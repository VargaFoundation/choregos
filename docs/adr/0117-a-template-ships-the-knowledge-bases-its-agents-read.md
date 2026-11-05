# 0117 — A template ships the knowledge bases its agents read

- **Status**: accepted, 2026-11-02 — S51-06. Extends [ADR 0051](0051-knowledge-bases.md) (bases were made
  by hand only) and [ADR 0093](0093-an-agent-ships-as-a-plugin.md) (a plugin ships what its templates ship)
- **Concerns**: `template.schema.json` (`defaults.knowledge`), `choregos_api.services.template_knowledge`,
  `services.installation.install_what_template_delivers`, `knowledge.sync.open_source` /
  `github_app_credential`, `PATCH …/sources/{id}`, `choregos_core.plugins` (`Plugin.warnings`),
  `choregos plugin validate`

## Context

An agent names the knowledge bases it reads by slug (`spec.knowledge: [{slug}]`). The run resolves
them when it starts and stops when a base does not exist or is not served to its project
(`activities/stage.py`, `_knowledge_of_agent`). Bases were created only through the API, the console or
`choregos knowledge apply` (`kind: KnowledgeBase`). A template — and so a plugin, whose templates are
how it reaches a project (ADR 0093) — could ship agents naming a base, never the base: every project
born of it started with runs that could only fail until someone created the base by hand, with the right
slug, served to the right project, with the right sources.

## Decision

1. **`defaults.knowledge` in the template manifest**: a list of bases in the shape of the CLI's
   `kind: KnowledgeBase` manifest, less what the project decides. Each entry has `slug`, `name`,
   `description`, `serving_mode` and `sources`; each source has the API's fields (`name`, `kind`,
   `connector`, `config`, `interval_minutes`, `refresh_mode`, `refresh_cron`, `refresh_timezone`). No
   `scope`, `projects`, `groups` (the base belongs to the project), no `uploads` (a template's files are
   not documents), no eval set. One shape for "a base as code", not two.

   ```yaml
   defaults:
     knowledge:
       - slug: product-docs
         name: Product documentation
         serving_mode: index
         sources:
           - name: Repository docs
             kind: git
             config: { url_from_input: repo, ref: main, paths: ["docs/**/*.md"] }
             interval_minutes: 60
   ```

2. **Git sources only**, for now. A git source names its repository by `config.url` (https) or by
   `config.url_from_input`, a template input: `owner/name` becomes `https://github.com/owner/name`, an
   https URL is kept, and a `github-repo` input that was not given is the project's repository
   (`config.repo.url`) — the console creates projects with a repository and no inputs. SharePoint,
   Confluence and a site behind a login need an organisation connector a template cannot know; a public
   web source needs a reachability check at creation that would make a project's birth depend on a
   third-party site.

3. **When a project is born of the template** (the same transaction that installs its agents and skills,
   S20-07), each base is:
   - **created** when the organisation has no base of that slug: `scope: projects`, served to the project,
     owned by it (`owner_project`, so its owners curate it, S39-14), `created_by: template:<name>`; its
     sources are attached with `next_sync_at` empty and their sync loops start **after the commit**
     (`after_commit`, ADR 0085) — the loop's first pass syncs; the worker restarts a lost start;
   - **completed** when the project already owns it (the template applied again): the sources it lacks,
     matched by name, are added; nothing is duplicated (ADR 0008);
   - **reused untouched** when it is already served to the project (an organisation base, or one an
     administrator served): the template never rewrites the organisation's base;
   - **served** to the project when another project of the same template created it and none of its
     sources reads a project's repository: the template's own material, the same for every project;
   - **left alone** otherwise — a base someone else curates and does not serve here, an archived or
     restricted one, or one that reads another project's repository. The project is born without it, the
     audit log says why (`template.knowledge.skip`), and its runs stop with today's message until an
     administrator serves a base. A template never widens a base's reach to a project that did not have
     it, except its own material.

   A source whose repository cannot be resolved (an input with no value and no project repository) is
   skipped the same way. A manifest whose `defaults.knowledge` is wrong (a source reading an undeclared
   input, a base twice, a cron under five minutes) creates no project (422), as a wrong workflow does.

4. **The project's own repository is read through the deployment's GitHub App.** A source without a
   connector whose URL is the project's GitHub repository is stored with `github_app: true`. At each sync
   the platform mints a one-hour installation token for that repository only, `contents: read`, through
   the installation the organisation claimed on its owner (ADR 0073; a single-organisation instance still
   discovers it). No request can set the flag: `POST …/sources` drops it, and `PATCH` keeps it only while
   the URL stays the same. Any other private repository names an organisation `documents/git` connector.
   The organisation's network ceiling and egress allowlist apply at every sync, as for any source; a
   refusal shows on the source, it does not refuse the project.

5. **Plugin validation** checks `defaults.knowledge` against the schema and that each `url_from_input`
   names an input of its template, and **warns** — never refuses — when an agent of the plugin reads a
   base no template of the plugin ships: it runs only in an organisation that already has that base,
   served to the project. `choregos plugin validate` prints the warning.

## Consequences

- A plugin that ships agents reading a base now ships the base, and a project born of its template
  works on its first work item, once the first sync has run.
- Slugs are global to an organisation: two projects of one template that read each one's repository
  cannot both have `product-docs`. The second is born without it, and the audit says so; serving it is
  an administrator's decision, because it would show one project's documents to the other.
- A template applied again (a project whose workflows are reset) restores a source that was deleted
  from its base. Removing a template's source for good means removing it from the template.
- A plugin upgrade gives the projects already born of its templates the bases the new version ships
  (amendment, S51-06b): a project is born once, so without it an agent the upgrade brings refused every
  run on a base its project never received (seen in production: `fon`, born before `fondation-requests`
  0.6.0 shipped `infra-docs`). The same idempotent path as a birth; the `plugin.upgrade` audit entry
  says what each project received. Upgrading to the version already installed runs the same path and
  nothing else (`plugin.knowledge` audit entry): the repair for a project born before the fix. Archived
  projects receive nothing.
- Inputs given only at provisioning (`POST /projects/{id}/provision` with `inputs`) arrive after the
  bases were installed; a `github-repo` input falls back to the project's repository.

## Alternatives discarded

- **A provisioning step `knowledge.ensure_bases`**: steps run in the orchestrator from templates on
  disk only (a plugin's template has no steps there), and a project without steps is never provisioned.
- **A per-project slug (`product-docs-<project>`)**: the agent names one slug; rewriting the agents'
  specs per project would fork them.
- **Serving an existing base of that slug in every case**: a template would widen a base an
  administrator restricted to some projects, or show one project's repository to another.
- **Refusing the project when the base cannot be installed**: the base is the organisation's decision;
  the project is born, its runs say what is missing, and the audit says why.
