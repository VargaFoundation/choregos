# The development project on the dev tenant

`scenario_dev.py` plays the `github-software-delivery` template on the dev tenant, with a real repository
([DiametralGroup/choregos-sandbox-dev](https://github.com/DiametralGroup/choregos-sandbox-dev): a
small invoicing library, its tests, GitHub Actions), real agents of the catalog and real
decisions. The same course, on fakes and in accelerated time, is
`apps/orchestrator/tests/test_software_delivery_template.py`.

## What the dev tenant needs

- A version of the platform that carries S21-21 to S21-24 (the train that warns the work item,
  approval by workflow, the template, `cd: demo`), vendored in `choregos-deploy`.
- **The fakes served by a pod** (`demoFakes.enabled: true`, already true for the HR scenario): the
  train promotes in `/cd/mcp`.
- **Choregos' GitHub App installed on the sandbox repository** - a gesture in the GitHub console;
  its installation id goes in `TRIAL_INSTALLATION`.
- **Who decides**: the groups `maintainers`, `product-owners`, `release-captains`, `architects`
  say whom a human step is addressed to; deciding only takes the `developer` role (or more)
  on the project, and the departure to production the `release_captain` role. An administrator can do anything.
- The protection of `main` on the sandbox: required checks (`ci / test`), **no review required**
  (the platform merges, people decide in Choregos).

## The course

1. `prepare`: the `dev` project born from the template, its connectors (`github-issues`, `github`
   without a merge queue, `cd: demo` towards the fakes), the backend and profiles the dev tenant serves, the
   `smoke_ok` fact (`python -m invoices.smoke`); then `requirements`, which exits with an error if a
   connector is missing. Saving a connector tests it (S40-05): after `prepare`, `cd: demo` reads Connected, not Unknown.
2. `open bug`, `open adr`, `open feature`: a labelled issue in the sandbox - the
   webhook makes it a work item of the right workflow.
3. `follow <id>`: the state, and the address where to decide. **Decisions are taken in the console**,
   by a re-authenticated person: the feature's spec and PR, the decision on the ADR;
   the departure of the feature's prod train, on the train's page.
4. `verify <id>`: each run (its catalog agent, its transition, its cost), each decision,
   the PR. The exit code says whether the work item finished.

What to expect: the bug reaches `verified` without a human decision; the study stops at
`awaiting_decision`, then, approved, merges `docs/adr/0001-*.md` as `accepted` without a train; the
feature waits three times for a person, goes through staging, then waits for its captain.

The API token (`CHOREGOS_DEV_TOKEN`) is written nowhere: it comes from the environment.

## Reseeding the sandbox

Each issue is played against a defect the sandbox was seeded with; once its pull request is merged,
the defect is gone and `open` refuses to open the same issue again (it would cost a run to find
nothing to change). Putting the three defects back is a commit on the sandbox's `main` whose first
line starts with `chore(seed):` - `open` only counts the pull requests merged after it:

    git checkout 9c3492c6 -- src tests docs/adr
    git rm docs/adr/0001-keep-synchronous-invoice-sending.md
    git commit -s -m "chore(seed): put the credits, currencies and ADR defects back"

`9c3492c6` is the last commit before the first work item ran: `compute_total` ignores the credits,
there is no `compute_totals`, and no ADR 0001.
