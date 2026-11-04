# Product review — 2026-10-07

Vincent walked through the console of the dev tenant (chart 0.16.3) and found it "unclear to
use". This review says why, starting from the code, and what the S21 stream fixes. It is
critical on purpose: a product shown to customers must hold up in front of a user who did not
write it.

## What holds up

The engine is solid: durable, replayable workflows (Temporal, archived histories), actions
governed key by key with compensation, a policy that only tightens, costs measured in the
ledger, execution proven on dev (27/09: real model and tool, ticket carried through to
`fini`). The rule "nothing is ✅ without a test that fails in its absence" is kept.

## What does not hold up, from most to least serious

1. **Part of the announced governance is only declared.** Measured in the code on 07/10:
   - a train approval written INSIDE a workflow (`train: {approval: captain}`) is read by
     nobody: `_run_train` (`interpreter.py`) only passes the environment, `signal_train`
     sends `labels: []`, and `ReleaseTrain._depart` reads the approval only in the project's
     POLICY;
   - `review.humans`, `policy.approvals.merge|prod` and `train.auto_sync` are read nowhere;
   - the train never tells the ticket that it has shipped: the ticket waits for a `cd.*` that
     nothing sends (the tests inject it by hand) — in production, it ends in `needs_human` on
     timeout.

   For a product whose promise is "measured guarantees", this is the most serious point:
   a user who writes "the production release waits for a captain" in a workflow believes
   they are protected. S21 fixes it (approval per release, the train tells the ticket).
2. **Too many exposed notions, with overlapping names**: workflow, actor, role, playbook,
   registry agent, implicit agent, runtime/backend, skill, project connector, organisation
   connector, operation, tool catalogue, integration, MCP gate, action, proposal,
   approval, human request, task, train, guarantee, finding, memory. No screen says
   how they fit together; `/admin/connectors` showed a directory, a fleet and a
   provider's agent served over MCP side by side, flat.
3. **The main use case (software) runs on invisible agents.** The delivered workflows
   name roles, not registry agents: no measures, no budget per agent,
   no versions, no catalogue. `/agents` only showed the two HR coordinators.
4. **No "first ten minutes".** A new project does not run until its connectors and
   its human groups exist, and nothing says so — even though
   `GET /projects/{id}/requirements` already computes it.
5. **Human stages addressed to groups that nobody checks** (`product-owners`,
   `release-captains`, hard-coded in the templates). A group unknown to the IdP means a
   ticket that waits forever, in silence. And no page says "what is waiting for me".
6. **Two parallel tool systems**: `config.tools` (catalogue) next to the organisation
   connectors and their per-operation policy — obstacle no. 11 of 27/09 cost
   hours. One project connector per kind (`UniqueConstraint`) contradicts ADR 0034.
7. **Writing a workflow, the central value of the product, is fragile**: broken YAML editor
   (Monaco downloaded from a CDN that the CSP blocked), a map unreadable beyond ten states,
   two editing paths that overwrote each other without saying so, actors that cannot be edited from the map.
8. **Mixed language and insider vocabulary**: French in an English interface (state
   names, map labels, validator and API messages); everything in lowercase;
   "born closed", "by reference", "acts as" with no help; raw error codes
   (`workflow.effet_implicite`).
9. **One has to come to the console.** Teams live in GitHub, Jira and Slack; the
   `/choregos …` commands are parsed but trigger nothing (S3-03 🟡), and there is no
   email.
10. **The quality of the agents on dev**: opencode and the platform's `cheap`/`standard`
    profiles only. A real development ticket on a small model gives a
    weak result: in a demonstration, that is a bigger credibility risk than any screen
    defect.
11. **A demo made by hand**: the dev `billing` project was created by hand; nothing
    recreates a coherent demonstration organisation.

## What the S21 stream fixes

| Point | Stories |
|:--|:--|
| 1 — declared governance | S21-21 (the train tells the ticket, approval per release, ADR 0041) |
| 2 — notions | S21-03 (menu, glossary), S21-04 (connectors in sections) |
| 3 — invisible agents | S21-15 to S21-19 (agent catalogue, external clients, templates that name agents; ADR 0040) |
| 7 — writing a workflow | S21-01, S21-05 to S21-07 (grids, editor, single draft, flat map; ADR 0038) |
| 8 — language | S21-02, S21-09 to S21-14 (ADR 0039) |
| development project | S21-20 to S21-25 (`github-software-delivery` template: `dev-simple`, `study`, `dev-complex`) |

## Proposed next, in order

- **P1 — real use**: a getting-started checklist per project (from `requirements`: repository, CI,
  CD, runtime, gateway, groups); a "my work" page (everything that is waiting for me, across
  all projects); human groups verified at publication (unknown or empty: warning);
  `make demo-dev`, which recreates the demonstration organisation in English.
- **P2 — adoption**: a single tool system (`config.tools` deprecated in favour of the per-operation
  policy) and several connectors per kind; `/choregos answer|retry` from GitHub and
  Jira (decisions stay in the console, ADR 0030); email; the policy as a
  form; the real Argo CD chain (the GitOps PR that `promote` opens is merged by
  nobody); a `strong` profile on the dev gateway (platform gesture).
- **P3 — writing**: a pass over the texts (sentence case, complete glossary, readable
  errors).
