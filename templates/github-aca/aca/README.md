# Agent runs on Azure Container Apps

One run = one manually triggered ACA *job*, created by the orchestrator and removed by the
resource's lifecycle. This folder documents what the executor creates, so that an Azure
administrator can audit it or pre-provision it by hand.

## What Choregos creates per run

```jsonc
// PUT /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.App/jobs/run-{run_id}
{
  "location": "westeurope",
  "identity": { "type": "UserAssigned", "userAssignedIdentities": { "<identity_id>": {} } },
  "properties": {
    "environmentId": "<environment_id>",
    "configuration": {
      "triggerType": "Manual",
      "replicaTimeout": 5400,          // the stage's minutes budget
      "replicaRetryLimit": 0,          // retries are decided by the orchestrator
      "manualTriggerConfig": { "parallelism": 1, "replicaCompletionCount": 1 },
      "secrets": [{ "name": "run-token", "value": "<token scoped to this run>" }],
      "registries": [{ "server": "<registry>", "identity": "<identity_id>" }]
    },
    "template": { "containers": [{ "name": "runner", "image": "<runner@sha256:…>", "args": ["run"] }] }
  }
}
```

Then `POST .../jobs/run-{run_id}/start`. Both calls can be replayed: the `PUT` is idempotent,
and the `start` only happens if the job has no execution yet.

## What Choregos does not do

- **No cloud credential in the runner.** The image is pulled by the job's managed identity;
  the container has neither a subscription key nor an ARM token.
- **No secret in clear in the definition.** The run's token goes through a job secret:
  `az containerapp job show` does not return it.
- **No implicit scale-out.** `parallelism: 1`: one run, one execution.

## Required rights

| Principal | Role | On |
| :-- | :-- | :-- |
| the orchestrator's identity | `Contributor` (or a custom `Microsoft.App/jobs/*` role) | the jobs' resource group |
| the job's managed identity | `AcrPull` | the runner image's registry |
| the orchestrator's identity | `Log Analytics Reader` | the workspace, for `logs()` |

## Logs

ACA does not expose logs through ARM. Without `log_analytics_workspace_id`, `logs()` returns a
line that says where to look rather than silence:

```kql
ContainerAppConsoleLogs_CL
| where ContainerGroupName_s startswith 'run-<run_id>'
| project TimeGenerated, Log_s
| order by TimeGenerated asc
```

## Known limits

**Application** deployment stays on Argo CD: what moves to ACA is the execution of agents, not
the deployment target. A fully Azure template (Azure Boards, Azure Pipelines, ACA revisions as
the target) would need two adapters that do not exist yet — see `docs/plan/BLOCKERS.md`.
