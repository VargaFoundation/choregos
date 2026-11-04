# Platform trial on the dev tenant

What the trial could not prove locally, tried on Diametral's dev tenant
(`http://choregos.internal.dev.diametral.com`, behind NetBird):

- **a real agent** (opencode, a model of the platform's gateway) picks `finding_search` and
  `host_get` by itself, then proposes `open_infra_pr` (elements 3 and 4);
- **a real PR**, opened by the platform on a sandbox repository, with the tenant's GitHub App
  (element 5);
- **a real re-authentication** through Keycloak: the decision carries the IdP's `auth_time`
  (element 5, and issue #153 tried against a real IdP).

## What the tenant needs

1. Choregos version 0.13.0 (the extension ships in it, inactive).
2. In `choregos-deploy`:
   - the vendored 0.13.0 chart;
   - `global.extraEnv: [{name: CHOREGOS_ONTOLOGY_TRIAL, value: "1"}]` to activate the extension;
   - the `infra_analysis.md` playbook in a ConfigMap named by `global.playbooks.configMap`.
3. A **sandbox repository** where the tenant's GitHub App is installed (contents and PRs
   writable). The paths the action allows are `platform/**` and `gitops/**`.

## The course

| Step | Who | How |
|---|---|---|
| 1 | A human | Sign in to the console, create an API token (`POST /api/v1/me/tokens`), keep it out of the repository |
| 2 | The script | `scenario_dev.py prepare`: project `essai-it4it`, connectors (`internal`, `github`), workflow `essai-it4it`, IT4IT ontology, two hosts through `register_host`, a report of findings, then a started work item |
| 3 | The agent | Investigates the finding with the generated tools and proposes `open_infra_pr`; the work item moves to `propose` |
| 4 | A human | Re-authenticates (`/api/v1/auth/login?reauth=1`), then approves the proposal (below) |
| 5 | The platform | Writes the file on `choregos/<proposal>` and opens the PR; the proof records it |
| 6 | The script | `scenario_dev.py verify`: the PR, the decision and its `auth_time`, then the collector's re-run |

Script variables: `CHOREGOS_DEV_URL`, `CHOREGOS_DEV_TOKEN`, `CHOREGOS_DEV_ORG`, `TRIAL_REPOSITORY` (see its header) - none assumes a tenant.

**Approving (step 4)**: a decision requires a human session. An API token is refused (403
`decision_requires_session`), and that is intended. After `?reauth=1`, in the browser console, on
the platform's page:

```js
await fetch("/api/v1/projects/<project>/proposals/<proposal>/decision", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({decision: "approve", reason: "diff re-read"}),
}).then(r => r.json())
```

## What this course does not prove

- **the console**: the approval box (SOC-134) does not exist yet, the decision goes through the API;
- **the real collector**: the report is posted by the script, in the observations NDJSON v1 format;
- **an applied fix**: nobody merges the PR, and the collector's re-run is simulated.
