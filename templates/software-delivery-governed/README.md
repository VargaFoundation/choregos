# software-delivery-governed — every change through every check

One workflow, `governed-delivery`, for a team whose changes must pass architecture, QA, security,
supply chain and compliance before they reach production. It is run by the platform's catalogue
agents ([ADR 0040](../../docs/adr/0040-un-catalogue-d-agents-integre.md)): they enter the organisation
when the first project is born from this template, and the organisation can then publish its own
versions of them.

| Step | Agent or people | Output | Gate |
|:--|:--|:--|:--|
| triage | `triager` | size, risk | — |
| study | `researcher` | `research_markdown` | — |
| architecture | `architect` (cites the knowledge bases attached to it) | `adr_path`, `adr_markdown` | **architects approve** |
| specification, plan | `spec-writer`, `planner` | `spec_markdown`, `allowed_paths`, `plan_markdown` | — |
| implementation | `developer` | the branch | scope, no secrets, diff size |
| QA | `qa` (edge cases, mutation testing) | `test_plan`, `mutation_report`, `test_report` | evidence present |
| security review | `security-reviewer` | `security_review_markdown`, `verdict` | verdict `approve` |
| supply chain | `secops` (trivy, gitleaks) | `secops_report`, `sarif`, `verdict` | verdict `approve` |
| compliance | `compliance` (never legal advice) | `compliance_review`, `verdict` | **compliance officers decide** |
| documentation | `docs-writer` | `docs_report` | scope, no secrets |
| release readiness | `devops` | `rollout_plan`, `verdict` | verdict `approve` |
| pull request | the platform, then **maintainers approve** | — | CI green to merge |
| staging, production | the release train; **release captains approve production** | — | ADR 0041 |
| verification | `devops`, from logs and metrics read through read-only connectors | `devops_report`, `verdict` | verdict `approve`; "nothing observed" goes to a person |

The checks after implementation run **one after the other**: the workflow DSL has no parallel
branches. Any of them can send the work back to the review responder with its report, and the
sequence runs again on what changed.

Things to know:

- **Documentation paths.** The documentation writer can only write inside the work item's allowed
  paths. Have the specification include them (`docs/**`, `CHANGELOG.md`), or the writer asks for them
  with `request_scope_change`.
- **Knowledge.** The architect and the compliance reviewer cite the organisation's framework and
  policies when a knowledge base is attached to them (ADR 0051); without one, they say so.
- **Observability.** The DevOps verification reads Loki, Prometheus or Grafana when the project has
  read-only connectors to them. Without any, its verdict is `comment` and a person decides.
- **Production** never moves without the train and the release captain: no agent deploys.

Proven in fakes mode by `apps/orchestrator/tests/test_governed_delivery_template.py` (one ticket from
the issue to production verified, and a compliance refusal sent back); not yet run with real agents on
a tenant.
