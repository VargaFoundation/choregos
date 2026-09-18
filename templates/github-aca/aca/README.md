# Runs d'agents sur Azure Container Apps

Un run = un *job* ACA à déclenchement manuel, créé par l'orchestrateur et supprimé par le
cycle de vie de la ressource. Ce dossier documente ce que l'exécuteur crée, pour qu'un
administrateur Azure puisse l'auditer ou le pré-provisionner à la main.

## Ce que Choregos crée par run

```jsonc
// PUT /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.App/jobs/run-{run_id}
{
  "location": "westeurope",
  "identity": { "type": "UserAssigned", "userAssignedIdentities": { "<identity_id>": {} } },
  "properties": {
    "environmentId": "<environment_id>",
    "configuration": {
      "triggerType": "Manual",
      "replicaTimeout": 5400,          // le budget minutes de l'étape
      "replicaRetryLimit": 0,          // la reprise est décidée par l'orchestrateur
      "manualTriggerConfig": { "parallelism": 1, "replicaCompletionCount": 1 },
      "secrets": [{ "name": "run-token", "value": "<jeton scopé à ce run>" }],
      "registries": [{ "server": "<registry>", "identity": "<identity_id>" }]
    },
    "template": { "containers": [{ "name": "runner", "image": "<runner@sha256:…>", "args": ["run"] }] }
  }
}
```

Puis `POST .../jobs/run-{run_id}/start`. Les deux appels sont rejouables : le `PUT` est
idempotent, et le `start` n'a lieu que si le job n'a pas déjà une exécution.

## Ce que Choregos ne fait pas

- **Aucun credential cloud dans le runner.** L'image est tirée par l'identité managée du
  job ; le conteneur n'a ni clé d'abonnement ni jeton ARM.
- **Aucun secret en clair dans la définition.** Le jeton du run passe par un secret de job :
  `az containerapp job show` ne le rend pas.
- **Aucune montée en charge implicite.** `parallelism: 1` : un run, une exécution.

## Droits nécessaires

| Principal | Rôle | Sur |
| :-- | :-- | :-- |
| identité de l'orchestrateur | `Contributor` (ou rôle custom `Microsoft.App/jobs/*`) | le groupe de ressources des jobs |
| identité managée du job | `AcrPull` | le registre de l'image runner |
| identité de l'orchestrateur | `Log Analytics Reader` | l'espace de travail, pour `logs()` |

## Logs

ACA n'expose pas les logs par ARM. Sans `log_analytics_workspace_id`, `logs()` rend une
ligne qui dit où regarder plutôt que du silence :

```kql
ContainerAppConsoleLogs_CL
| where ContainerGroupName_s startswith 'run-<run_id>'
| project TimeGenerated, Log_s
| order by TimeGenerated asc
```

## Limites connues

Le déploiement **applicatif** reste sur Argo CD : c'est l'exécution des agents qui bouge
sur ACA, pas la cible de déploiement. Un template entièrement Azure (Azure Boards, Azure
Pipelines, révisions ACA comme cible) demanderait deux adaptateurs qui n'existent pas
encore — voir `docs/plan/BLOCKERS.md`.
