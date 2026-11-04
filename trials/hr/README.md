# The HR scenario on the dev tenant

`scenario_hr.py` plays the `joiners-leavers` template on the dev tenant, with a real agent and
real decisions. The same scenario, in accelerated time and on in-memory fakes, is
`tests/e2e/test_m6_rh.py`: this one shows the same thing **deployed**.

## What the dev tenant needs

- A version of the platform that carries batch 6 (S20-01 to S20-07).
- **The fakes served by a single pod**: `demoFakes.enabled: true` in the dev tenant's values
  (choregos-deploy). The API runs in two replicas and the orchestrator apart; in-memory fakes would
  show them as many directories and device fleets as there are processes. The chart refuses it in
  staging and prod.
- Optional: a token for the fakes (`demoFakes.tokenSecret`), also set in the environment
  of the API and the orchestrator; its reference is then passed to the script (`TRIAL_TOKEN_REF`).

## The course

1. `prepare`: the organisation's five connectors (`directory` -> the Graph fake, `devices`,
   `carrier`, `badge-readers`, `supplier` -> the served fakes; names of template 2.0.0, formerly French), the supplier's tools
   discovered - closed -, what the organisation allows without a human decision, and the `rh` project.
2. `arrival`: the request, through the MCP door, as an HR person's Claude. The arrival date is yesterday:
   the dev tenant does not skip time, D-10 and D-7 are past, D+1 is today. `arrival --in 30`
   shows the wait instead - and `move <work item> <date>` undoes it: the timer re-arms.
3. `follow <work item>`: what the work item waits for, and the address where to decide. **Decisions are
   taken in the console**, by a re-authenticated person: the plan's approval, the sensitive
   group, the laptop order; the "badge" task is done there too, and attested there.
4. `departure <arrival work item>`: the same person's departure, today (D0).
5. `verify <work item>`: each action, who decided it, its journal effect by effect, and what was
   attested - the evidence file. The exit code says whether everything succeeded.

The API token (`CHOREGOS_DEV_TOKEN`) is written nowhere: it comes from the environment.
