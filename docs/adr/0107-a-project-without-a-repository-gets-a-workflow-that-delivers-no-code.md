# 0107 — A project without a repository gets a workflow that delivers no code

- **Status**: proposed, 2026-10-28, at the product owner's request ("a project does not necessarily
  have a git repository")
- **Concerns**: `choregos_core/dsl/templates/` (a fourth core workflow, `default-request`),
  `services/definitions.ensure_defaults` and `services/gabarits.livraison` (which workflow a project
  without a template receives), the readiness items of S40-06, the project wizard; amends decision
  **D14** of `docs/plan/00-index.md`; follows [ADR 0012](0012-the-engine-is-not-tied-to-software.md)
  and [ADR 0029](0029-a-platform-for-governed-agents.md)

## Context

On 2026-10-28 the product owner created `foundation4foundation` without a repository and read, on its
readiness panel, eight items to do, three of which no gesture could ever close:

- **Repository set**, **Repository (SCM) connected** and **Continuous delivery connected** are not asked
  by the project. They are derived (`choregos_core.dsl.exigences`, ADR 0034) from the workflow the
  project received: `default-simple`, whose `dev` agent has the role `implement` (it works in a
  repository), whose `t-open-pr` and `t-merge` open and merge a pull request, and whose terminal state
  `deployed_prod` is `production: true` behind a release train.
- A project born **without a template** always receives `default-simple`
  (`gabarits.livraison` → `WORKFLOW_SANS_GABARIT`), whatever its configuration says. Decision D14
  ("`default-simple`; production always through the release train") was taken when every project was a
  software project. ADR 0012 and ADR 0029 have since made the engine general-purpose, and the
  `joiners-leavers` and `investment-research` templates prove workflows without a repository run — but
  they are only reachable by choosing that template, and they belong to one trade each.
- S40-06d (the change before this record) made such a project activatable and made its readiness
  explain *which workflow* asks for a repository. It did not change the workflow: that is this
  decision.

The three core workflows (`default-simple`, `full-auto`, `advanced`) all deliver code. There is no
neutral one.

## Decision

1. **The core ships a fourth workflow, `default-request`**, that needs no repository, no CI and no CD.
   Its shape is the smallest governed request:

   `inbox` → *refine* (an agent writes a brief and the expected deliverable) → *approve the brief*
   (a human of the `product-owners` group) → *deliver* (an agent produces the deliverable, using the
   project's knowledge bases and allowed tools) → *accept* (the same human group accepts it or sends it
   back with a reason) → `done`; with `needs_human` and `abandoned` as in `default-simple`.

   Its agents are registry agents named by `agent:` (ADR 0040 §5, installed when the workflow is
   published): the existing `spec-writer` for the brief, and a new catalogue entry **`assistant`**
   (role `deliver`) for the deliverable, which ships with its eval like every entry (ADR 0040). The
   deliverable is a declared text output (`deliverable_markdown`), not a file: the workflow then needs
   no object store either.
2. **The guard is mechanical**: a core test asserts that `exigences(default-request)` is exactly
   `{tracker, runtime, gateway}` — a later edit that adds a role working in a repository, a production
   state or a pull request turns it red. "Delivers no code" is a property the suite checks, not a
   description.
3. **Which workflow a project without a template receives** is decided once, at birth, by what the
   project says about itself: a `repo.url` in its configuration → `default-simple` (D14 unchanged for
   software projects); no repository → `default-request`. A template's own `defaults` always win, as
   today. This is the only place the choice is made (`livraison`); the wizard, the API, the CLI and the
   MCP door all go through it.
4. **Never rewritten afterwards** (the rule of ADR 0040 §4 and of templates' workflows): adding a
   repository later does not swap the project's workflow, and an existing repository-less project that
   received `default-simple` keeps it. Readiness already names the workflow that asks for a repository
   and links to Workflows (S40-06d); from ADR 0108 on, that link opens the catalogue filtered on
   "needs no repository", where adopting `default-request` is one gesture.
5. **D14 is amended**, not superseded: `docs/plan/00-index.md` reads "`default-simple` for a project
   with a repository, `default-request` without one; production always through the release train".

## Consequences

- A project created without a repository is ready once its tracker is chosen: its readiness lists no
  repository, SCM or CD item, because its workflow asks for none.
- `TEMPLATE_NAMES` gains `default-request`, so the "new workflow" page offers it to every project.
- The `assistant` entry is generic on purpose; a trade that needs more (HR, finance, legal) brings its
  own workflow through a template or a plugin (ADR 0093), and the catalogue of ADR 0108 lists it.
- Stories S49-01 to S49-03 in `docs/plan/08-backlog.yaml`.

## Alternatives discarded

- **Make the repository mandatory.** Contradicts ADR 0012 and 0029, and the two templates that already
  run without one.
- **One workflow whose repository steps switch off when the project has no repository.** A workflow
  whose meaning depends on the project's configuration cannot be read, replayed or reviewed on its own;
  the YAML must say what happens (ADR 0037).
- **No default at all: the wizard must ask.** The wizard will ask (ADR 0108), but the API, the CLI and
  the MCP door create projects too, and a project with no workflow receives nothing.
- **Pick the workflow from the tracker type.** A tracker says where requests live, not what the work is.
