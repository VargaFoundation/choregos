# github-software-delivery — fix, study, feature

Three workflows for a software team on GitHub, run by agents from the platform's catalogue
([ADR 0040](../../docs/adr/0040-un-catalogue-d-agents-integre.md)). The agents enter the organisation
when the first project is born from this template; the organisation can then tune them by publishing
its own versions.

| Workflow | For | Humans |
|:--|:--|:--|
| `dev-simple` | a bug or a small fix | none, unless something goes wrong |
| `study` | an in-depth study that ends with one decision record, MADR 4 | the architects decide |
| `dev-complex` | a feature, or anything that is not small | a product owner on the specification, a maintainer on the pull request, a release captain on production |

Routing, first match wins: `study`, `adr`, `spike` → `study`; `feature`, `epic`, `complex` →
`dev-complex`; `bug`, `fix`, `chore` → `dev-simple`; anything else → `dev-simple`. The provisioning
creates these labels in the repository.

## What each workflow does

**dev-simple** — the triager sizes the issue; anything that is not small (size S or M, risk low or
medium) stops and waits for a person, who moves it to `dev-complex`. The developer implements, the
tester runs the checks and writes the missing tests (a failure goes back to the developer with the
report), a reviewer with a fresh context approves or asks for changes (a review responder handles
them), the platform opens and merges the pull request once GitHub Actions is green (a CI fixer
handles a red run), the release train deploys to production **without approval**, and the
production verifier gives a go or no-go from measured facts.

**study** — the spec writer frames the question and the decision criteria, the researcher gathers
options with `path:line` evidence, the architect writes **one** ADR in `docs/adr/` with the `madr-4`
skill. The guarantee `markdown_sections` reads what the branch adds — context, considered options,
decision outcome, `Chosen option: "…"` — not what the agent says it wrote. A critic reviews it, the
`architects` group decides (a rejection goes back to the research, with its reason), the architect
sets `status: accepted`, and the pull request is merged — once `adr_number_free` confirms that no
record on the default branch already carries its number. Two studies running together pick the
same "highest plus one": the second to merge is sent back to the architect, who renumbers it.
**Nothing is deployed.**

**dev-complex** — the same chain as `dev-simple`, plus a plan, a security review and release notes
(they are added to the pull request). Three people decide: `product-owners` on the specification,
`maintainers` on the pull request — again after a CI fix, since what changed has not been seen —
and `release-captains` on the production departure of the release train
([ADR 0041](../../docs/adr/0041-l-approbation-suit-ce-que-le-lot-emporte.md)). Staging comes first,
with no approval.

No agent deploys: the release train does, under the project's policy. A batch that carries a
`dev-complex` work item waits for its captain; a batch of fixes alone leaves on its own.

## Before the first work item

1. **Install the Choregos GitHub App** on the repository, and give the project its `tracker`
   (`github-issues`) and `scm` (`github`) connectors. Checks are read from the pull request: GitHub
   Actions needs no `ci` connector.
2. **Branch protection** on the default branch: required status checks, **no required review** —
   the platform merges, and the humans decide in Choregos. Set the `scm` connector's
   `merge_queue` to match the repository (`true` for a merge queue, `false` for a direct merge).
3. **A `cd` connector** for the release train: Argo CD in real life; the `demo` type on a
   demonstration tenant.
4. **Who decides**: the groups `maintainers`, `product-owners`, `release-captains` and `architects`
   name who a human step is for. Today, anyone with the `developer` role (or above) on the project
   can decide it in the console; the production departure needs the `release_captain` role.
5. **A production check**: set `dod.facts.smoke_ok` in the project's configuration to a command that
   exits 0 when production is healthy (for instance `curl -fsS https://app.example.com/health`), and
   add the domain to the policy's `sandbox.network.allow_domains`. The production verifier fails
   without it.
6. Label an issue `agent-ready` (plus `bug`, `adr` or `feature`) to start it.

The policy (`policy.yaml`) sets the budgets (10 / 30 / 80 / 160 USD per work item by size, 150 USD per
day), the trains (staging every 15 minutes on weekdays, production every half hour in working
hours), and never touches `.choregos/`, `.github/workflows/` or `deploy/`.

## Known limits

- With Argo CD, the train's promotion opens a GitOps pull request that nothing merges yet.
- Two studies running at once can pick the same ADR number; the second pull request then conflicts.
- The models are `profile:standard` (and `profile:cheap` for triage and release notes); a deployment
  that serves a stronger profile can raise the developer's.
