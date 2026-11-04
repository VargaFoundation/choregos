# The HR scenario on the dev tenant - results of 2026-10-06

**Question**: does the `joiners-leavers` template hold on the deployed dev tenant, in 0.16.1, as
it holds in the accelerated-time e2e (`tests/e2e/test_m6_rh.py`)?

**Answer, up to the first human decision: yes**, after a fix to the script. The rest -
the HR approval, the sensitive group, the laptop order, the "badge" task - waits for
people, in the console: the script decides nothing.

Names below are the French ones the tenant carried at the time of the run (template 1.x, before the
rename to English in template 2.0.0).

## What was played

1. **`prepare`**: the organisation's five connectors (`annuaire` -> the Microsoft Graph fake,
   `parc`, `transporteur`, `lecteurs`, `fournisseur` -> the fakes served by the pod
   `choregos-demo-fakes`), the supplier's tools DISCOVERED - the platform reached the fakes' pod
   from the cluster -, the policies, and the `rh` project. Born from the template: the workflows
   `onboarding` and `offboarding` (v1), the routing (`offboarding` on a label), the agents
   `coordinateur-onboarding` and `coordinateur-offboarding`, the skills `procedure-onboarding` and
   `profils-d-acces`, and - the ontology extension is active on the dev tenant - the registry
   `rh-arrivees-departs` (six object types, 28 MCP tools), installed by the template's extension
   (S20-09) under PostgreSQL and its RLS.
2. **`arrival`**: the request filed through the project's MCP door, with an `mcp:write` token created then
   revoked, as an HR person's Claude would.
3. **The agent's plan** (`t-plan`, `coordinateur-onboarding@1`): opencode, `platform/standard`,
   $0.21; the agent LOADED its two skills (ACP journal: `Loaded skill: procedure-onboarding`,
   `Loaded skill: profils-d-acces`), looked the account up in the directory through
   `choregos_annuaire__get_user` - the broker, the connector's key, the Graph fake -, and returned
   `plan_d_acces` ("Access plan prepared for the arrival of Lea Martin, no discrepancy detected"): the
   `outputs_present` guarantee passes, the work item waits for the HR approval.

## The defect found: a new project starts on `claude-code`

The first run (RH-1) saw `t-plan` fail twice, without a call to the model ($0, no
runner event, "Job has reached the specified backoff limit"), and the work item end
`a_revoir`. A new project gets `agent.default_backend: claude-code` and no model profile;
the dev tenant runs opencode behind the platform's gateway, as the IT4IT trial
configured it. `prepare` now sets the backend and the `standard` profile on every run
(`TRIAL_BACKEND`, default `opencode`; `TRIAL_MODEL`, default `platform/standard`); RH-2 passed.

What the defect says about the product: a template ships agents without saying what they run on, and a
project cannot inherit the backend its deployment can serve. An agent's version can
name its backend; a per-deployment default is missing.

## What waits for people (console, re-authenticated)

- the HR approval of the access plan (`t-validation`);
- then, automatically: the account and its group (D-10 is past);
- the sensitive group: a re-authenticated approval;
- the laptop order to the supplier's agent: an approval;
- the "badge" task: the UID typed in, the hand-over attested;
- the check on D+1; then `departure RH-2` and `verify RH-2`.
