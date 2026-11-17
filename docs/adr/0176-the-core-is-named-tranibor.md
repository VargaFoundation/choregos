# 0176 — The core is named Tranibor: the visible name first, then packages, images and variables, each behind a compatibility rule

- **Status**: accepted, 2026-11-17 (decided by the product owner on 2026-11-15, D6, and confirmed on
  2026-11-17: "do the renames now", the trademark gate being skipped); R1 is implemented by S75-01,
  R2, R3 and R4 are not started
- **Concerns**: the whole repository (25,000 occurrences of the old name in 4,600 files, counted on
  2026-11-17), and above all `packages/*` (the distributions and import packages), `publishing.json` and
  `tools/publishing_identity.py`, `charts/`, `docker/`, `infra/policies/`, the `CHOREGOS_*` variables;
  amends [ADR 0048](0048-the-console-owns-its-primitives.md) and
  [ADR 0064](0064-a-theme-is-data-the-console-applies.md) (the footer's name),
  [ADR 0079](0079-choregos-and-graal-are-autonomous-products.md) (the guard's allowed names) and
  [ADR 0156](0156-the-publishing-identity-is-one-file.md) (what the identity names next)

## Context

The company is **graal** (GRAAL.SYSTEMS SAS). Its commercial product is the **graal Agentic Platform**.
The Apache-2.0 core under it is named **Tranibor**, and that name is the one the repository carries
(`graalsystems/tranibor`, transferred and renamed on GitHub on 2026-11-17), the documentation, and, on
the agentic side, the CLI, the packages and the images. "Choregos" was the name of the project under the
Varga Foundation; the enterprise edition (an EE build that imports `choregos_api`) and the production at
`agentic.diametral.com` were built on it.

The old name is everywhere, in four layers that do not break alike:

| Layer | What it is | A rename breaks |
| :-- | :-- | :-- |
| Visible | prose, README, console strings, the CLI's help and version, the default product name | nothing but a test that spells the old string |
| Code names | the distributions `choregos-*`, the import packages `choregos_*`, the CLI entry point | the EE, the plugins and any script that imports or installs them |
| Artefact names | images `ghcr.io/graalsystems/choregos-*`, charts, release assets, Kyverno policies that pin those names | a pod that does not pull, a policy that refuses it, a signature that does not verify |
| Wire and state | environment variables, headers, URIs, manifest kinds, database and queue names, cookies | a running deployment, a stored row, a client in the field |

One commit cannot rename all four, and the fourth layer must not be renamed by anyone in this decision.

## Decision

1. **The name is Tranibor, in four rounds, each one a story and a PR, each one shipping alone.** A round
   never needs a later one to be green, and no round removes a name another party still reads: the old
   name stays a working alias for one deprecation window per round (below), then goes in its own PR.
2. **R1, the visible name (S75-01, this decision's first implementation).** The reader sees Tranibor:
   the README, the reference documentation and the documentation site, the console's strings and its
   default product name (`DEFAULT_THEME["product_name"]` and the console's own fallback), the text of the
   setup procedures, the strings the API, the orchestrator and the runner write for a person (the A2A
   card's name and provider, the MCP server's title, the headings of the comments written in a tracker,
   the messages), `NOTICE` and the trademark page. The CLI gains the command `tranibor`; `choregos`
   stays beside it as a second entry point of the same `main`, and prints nothing extra. `--version`
   prints `tranibor <version>`. **R1 renames no Python identifier, package, file path, environment
   variable, image, chart, header, URI or stored value.**
3. **R2, the Python distributions and import packages (not started).** `choregos-core` becomes
   `tranibor-core` and `choregos_core` becomes `tranibor_core`, and so on for `api`, `orchestrator`,
   `adapters`, `runner`, `cli`, `contracts`, `agent-sdk`, `outpost`, `playbooks`, `quant`, `tools-mcp`,
   `ontology` (directory names `packages/*` follow). For one minor cycle each old package stays as a
   **shim**: a module `choregos_<x>` that aliases `tranibor_<x>` in `sys.modules` (the package and every
   submodule, through a meta-path finder, so `import choregos_api.rbac` and `from choregos_core.dsl import
   validate` keep resolving to the same module objects and not to copies), and emits one
   `DeprecationWarning` naming the new import. Each old distribution is republished as an empty wheel that
   depends on the new one and ships the shim. The `choregos` console script keeps working through the
   shim. A test imports every public module through both names and checks the objects are the same; a
   second one fails when a new module is added to a package whose shim does not cover it. The enterprise
   edition and the plugins move at their own pace during the cycle; the shim is removed in the minor that
   follows, in its own PR.
4. **R3, the artefact names (not started).** Images and charts become `tranibor-*`
   (`tranibor-api`, `tranibor-web`, `tranibor-orchestrator`, `tranibor-runner`, `tranibor-outpost`,
   `tranibor-tools`, `tranibor-quant`, `tranibor-laya`, the charts `tranibor` and `tranibor-outpost`, the
   native CLI files `tranibor-<platform>`), through `publishing.json` and
   `tools/publishing_identity.py` (a name prefix next to `registry`, and `make publishing-rewrite`
   carrying it; ADR 0156 §7), never by hand. The old names are published as **aliases** for the whole
   window: every release pushes both tags of one digest, both signed by the same workflow identity (the
   repository did not change, so the cosign identity does not either), and both chart names.
   **Order, because an image that does not pull is the failure to avoid**: (a) infra admits the new
   names in its Kyverno policies, beside the old ones (`choregos-pod-security`,
   `choregos-image-tags`, `choregos-plugin-server-images` and the rest), and says so in its tests;
   (b) this repository publishes both names; (c) `choregos-deploy` switches its values to the new names,
   one environment at a time, dev first; (d) `choregos-ee` rebuilds on the new base image and the new
   packages (R2's shim lets it do that after, not before); (e) the old names stop being published two
   minors after (d), and the policies drop them last. The install scripts the API serves and the
   self-update accept both release asset names for the window. The Postgres database and role names, the
   Kubernetes namespace and the Helm release names are **deployment values** and stay what each
   deployment chose.
5. **R4, the environment variables (not started).** `TRANIBOR_*` is read first and `CHOREGOS_*` is the
   fallback, per variable, in one place: the four settings classes that carry `env_prefix="CHOREGOS_"`
   (API, API egress, orchestrator, runner) take both prefixes through one shared helper in the core, and
   the direct reads (`os.environ`, 199 files) go through it. When only the old name is set, the process
   logs one warning per variable naming the new one (never the value). When both are set and differ, the
   new one wins and the warning says so. The charts render `TRANIBOR_*` and the helper accepts what
   existing values files still set, so a production's Helm values with `CHOREGOS_*` keep working until it
   moves them. A test lists every variable of the repository and refuses one that is read by a path that
   bypasses the helper. The fallback is removed in the minor after the shims (R2), in its own PR.
6. **What is deliberately not renamed, in any round**, because a party outside this repository (or a row
   already written) holds it, and a rename needs that party to move first; each can later gain a second
   name beside the first, through a contract change, never by replacing it:
   - **A2A and MCP wire names**: the extension URI `https://choregos.dev/a2a/extensions/skill-fields/v1`,
     the UUID namespace `https://choregos.dev/a2a/task` that derives task ids (changing it changes every
     id), the MCP server's `name` (`choregos`; its `title` follows R1) and the `choregos.dev/problems`
     problem types.
   - **Manifest `apiVersion` and kinds**: `choregos/v1` (149 uses) and `choregos.dev/v1alpha1` (75), and
     the schema identifiers `choregos.dev/schemas/*`: manifests are written by customers and stored.
   - **HTTP headers**: `X-Choregos-Secret`, `-Signature`, `-Tool-Origin`, `-Script`, `-Digest`, `-Embed`,
     `-Token`, `-Egress-Key`, `-User-Token`, `-Theme`, `-Identity`, `-Event-Id`: callers and signatures
     are built on them. Webhook signatures never change name.
   - **Cookie names**: `choregos_session`, `choregos_org`, `choregos_saml`, `choregos_oidc`,
     `choregos_forge_link`: renaming signs every person out and breaks the embed.
   - **Marker comments written in a tracker**: `<!-- choregos:origin -->`, `choregos:delivered`,
     `choregos:already-fixed`: the platform finds its own comments by them (the headings beside them are
     visible text and follow R1).
   - **Kubernetes labels and annotations** `choregos.dev/*` (plugin, server, org, the egress and plugin
     server families): selected on by the policies and the network policies.
   - **Stored and addressed state**: the database name, role and schema, Alembic revisions and their
     history (applied migrations are history, ADR 0156 §5), the Temporal task queues and workflow types
     (a queue renamed while a run is open strands it; a workflow type renamed breaks its replay, ADR
     0008), the Temporal namespace, and the sealed-secret and Infisical paths.
   - **Third-party-visible tokens**: the `User-Agent` (`Choregos/<version>`) and the `robots.txt` tokens
     (`Choregos`, `ChoregosKnowledge`) of the web fetch: a site that allowed or blocked us is keyed on
     them. A new token is added beside the old, never in its place, and only with R4's window.
   - **The OpenAPI document's title** (`packages/contracts/openapi.yaml`, "Choregos API"): the contract
     moves by a `contract-change` PR (ADR 0001), which carries it.
   - **The commit identity** (`choregos-bot@…`, ADR 0156 `commit_email`), the GitHub App and bot names
     already registered, and the **documentation address**: they are values the publisher chooses, and
     change by editing `publishing.json`.
   - **History**: the ADRs, `docs/plan/`, the trial results, the French archive. They say what was true.
7. **A guard for each round, and each turns red without its change.** R1: the CLI exposes `tranibor`, the
   default product name is Tranibor, and a test lists every tracked prose file and refuses an old-name
   mention in user-facing text that is not on a short reasoned list. R2's tests are named in point 3, R3's
   are `make publishing-check` (it must know the new prefix) and a chart test that renders both names,
   R4's in point 5.
8. **Attribution stays what it is until the owner says otherwise.** "by Varga Foundation" in the console
   footer and in `NOTICE` is a legal statement of who holds the copyright, not a product name, and the
   repository's owner changed on 2026-11-17: whether the line becomes "GRAAL.SYSTEMS" is the owner's
   decision, tracked as an open question in STATUS, not made by a rename.
9. **The enterprise edition is not "Tranibor Enterprise".** The commercial product is the graal Agentic
   Platform; the trademark page says so, and the console's footer line for an edition that names a
   publisher no longer prints the core's name as the edition's own.

## Amendments to earlier records

- **ADR 0048 and ADR 0064** (the footer): the line reads "Tranibor by Varga Foundation · Apache 2.0", and
  "<product>, built on Tranibor by Varga Foundation" for a renamed instance. An edition that names its
  publisher reads "<product> · Enterprise edition by <publisher> · built on Tranibor by Varga
  Foundation (Apache 2.0)". It is still not themable. A theme an organisation stored with the product
  name "Choregos" keeps that name until the organisation changes it: a stored value is data (point 6).
- **ADR 0079** (the guard): its three allowances do not move. The edition's product name stays branding,
  and the guard's pattern is written in terms of "graal", which this decision does not touch.
- **ADR 0156** (the publishing identity): the definition keeps eight values. R3 adds the product's image
  and chart prefix to it, so that the next rename is one edit and one command; until then the names
  `choregos-*` in `publishing.json`'s derived files are what ADR 0156's last amendment already says: "the
  product's own names are not renamed here".

## Consequences

- A reader sees one name in the documentation and the console from R1, while every install, import and
  deployment keeps working unchanged; a person who types `choregos` still gets the CLI.
- The documentation says `choregos` for a command only where the native binary is meant (its release
  assets keep their names until R3), and says so once, in the CLI reference.
- Three rounds remain, each with a deprecation window and a removal PR: the work is longer than one
  find-and-replace and the production never sees a flag day.
- An organisation theme, a stored comment heading and an agent's already-written tracker comments keep
  the old name: a rename never rewrites data.
- R1's guard is a list of allowed old-name mentions in prose: it shrinks as R2 to R4 land, and an item
  that leaves the list is a PR that removed it.

## Rejected alternatives

- **One flag day.** 25,000 occurrences, four layers, one production: a single missed header, queue or
  image name is an outage, and the EE could not follow in the same hour.
- **Rename the wire contracts to match.** The manifests, signatures and cookies belong to the people who
  wrote them; a name we are tired of is not a reason to break them. They gain a second name only when a
  customer needs one.
- **Keep Choregos as the technical name for good.** It would leave two names in every error message, log
  line and image tag for a reader who knows only one.
- **One PR for the decision and R1.** Reviewing a 400-file mechanical rename while the plan it applies is
  still being read hides both; the decision is its own PR and R1 stacks on it.
