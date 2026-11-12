# 0156 — The publishing identity is one file

- **Status**: accepted, 2026-11-12; S69-01 implements it
- **Concerns**: `packages/core/src/choregos_core/publishing.json` and `publishing.py`,
  `apps/web/src/lib/publishing.{json,ts}`, `tools/publishing_identity.py` (`make publishing-check`,
  `make publishing-rewrite`), and every file it lists: the charts' defaults, `infra/`, the workflows,
  `CODEOWNERS`, the Dockerfiles and compose files, the plugin lock, the documentation

## Context

Who publishes Choregos, and where, was spelled out by hand in 148 tracked files: the image and chart
namespace, the source repository and its release pages, the user documentation's address, the security
contact, the address the agent commits as, the repository the platform's own plugins are built in,
their signed index, and the cosign identities that follow from those repositories.

Runtime code held its own copies: the orchestrator's default runner image, the `User-Agent` contact of
the platform's requests, the release pages of the CLI's self-update and of the API's downloads and
install scripts, the console's source link, release links and documentation base, the default
documentation address in two API modules, the runner's default commit address, the plugin lock tool.
Nothing said which copies had to agree, and nothing refused a new one. A downstream distribution, a fork
that publishes its own images, or any change of where the project publishes meant a search through the
tree, and a copy missed fails late: an image that does not pull, a signature that does not verify, a
link to nowhere.

The core names no operator, and the place it is published from is configuration, not code.

## Decision

1. **One definition.** `packages/core/src/choregos_core/publishing.json` holds eight values: `owner`,
   `registry`, `source_repository`, `documentation`, `security_contact`, `commit_email`,
   `plugin_repository`, `plugin_index`. It ships in the core's wheel and in the CLI binary (Nuitka
   includes `choregos_core`'s data). Each value is distinct, non-empty, without a trailing slash.
2. **Code reads it.** Python reads `choregos_core.publishing.PUBLISHING`, which also derives what
   follows from the values: the release pages and a release's page, an image reference, a file of the
   source repository, the signing identities. The console reads `apps/web/src/lib/publishing.json`, a
   byte-for-byte copy, through `apps/web/src/lib/publishing.ts`: the web image is built from
   `apps/web/` and cannot reach the core's file. The install scripts the API serves receive the release
   pages the way they receive the instance's address, by substitution.
3. **The cosign identities are not stored.** GitHub's keyless signing names the workflow that signed,
   `<repository>/.github/workflows/<file>@<ref>`, under `https://token.actions.githubusercontent.com`:
   the release workflow of `source_repository`, the publish workflow of `plugin_repository` on main.
4. **What cannot read a JSON file is a derived file**, listed in `tools/publishing_identity.py`
   (`DERIVED`): the charts' values, `Chart.yaml` and helpers, the Kyverno policies and their tests,
   the Argo CD Applications, the workflows, `CODEOWNERS`, the Dockerfiles, compose files and pipeline
   templates, the plugin lock, the contract's examples and the description of `docs_url` (with the
   types generated from it), the fixtures mirrored from published plugins, and the documentation pages
   a reader copies a command from. Code is never a derived file, unless a generator writes it from one
   (`packages/contracts/ts/api.ts`). Each derived file spells a value exactly as the definition does.
5. **History is never rewritten.** The ADRs, `docs/plan/`, the applied migrations and the trial
   results say what was true when they were written; they may name a former identity.
6. **The check refuses a new copy.** `make publishing-check` and `tests/tools/test_publishing_identity.py`
   read every tracked file for every value, in any case, plain or with its dots escaped once or twice
   (a regular expression in YAML), as a whole name; the owner counts only as a GitHub path segment
   (`<owner>/…`, `github.com/<owner>`), so a publisher's name in prose or in a product string is not
   it. They refuse: a file that spells one and is neither the definition,
   its copy, a derived file nor history; a derived file that is code, absent, or that no longer spells
   anything; a value spelled in another case than the definition's; a copy that differs.
7. **Moving it is one edit and one command.** Edit the definition, then
   `make publishing-rewrite FROM=<the commit before the edit>`: every value as it was at that commit is
   replaced by the current one in every derived file and in the copy, in one pass, longest first, then
   nothing of the former values may remain outside history. A test does it on a copy of the tree with
   every value moved.
8. **A name persisted in data is not an identity value.** The platform's plugin source is named
   `varga`: the chart's `global.plugins.sources[].name` and `defaultPlugins[].source`, and, since the
   migration `b8d0f2a4c6e1` (2026-10-28), the `source` column of `org_plugins` and
   `org_plugin_approvals`. Renaming it is a data migration of its own, released with the chart's two
   lists, not a value of this file.

## Consequences

- The defaults change with the definition; a deployment's own values (`global.imageRegistry`,
  `global.plugins.sources`, `CHOREGOS_DOCS_URL`, `CHOREGOS_USER_AGENT_CONTACT`, the runner's commit
  address) still win.
- The rewrite replaces, it does not duplicate: when images are published under a new identity while
  deployments still run images signed under the former one, the admission policies need both subjects
  and both image paths for that window, which is a change to the policies, written by hand.
- What the rewrite cannot do: create the GitHub teams `CODEOWNERS` names under the new owner, push the
  images and charts to the new namespace, re-sign the plugins under the new identity and refresh
  `plugins.lock` (`make plugins-lock`), or redirect the documentation site.
- The check is case-insensitive and matches whole names: a new value that also appears in unrelated
  text (an address used as an example, a registry path in a test) is reported there, and that text is
  reworded or listed.
