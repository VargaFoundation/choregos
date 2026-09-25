# Montée de version de Temporal

## Avant

1. Lire les notes de version : les montées de Temporal sont incrémentales, on ne saute pas
   une version majeure.
2. **`numHistoryShards` ne se change pas.** Il est figé à 512 depuis le premier jour.
   Le modifier invalide le cluster.
3. Vérifier que la CI de replay est verte : `uv run pytest tests/replay -q`.

## Pendant

```bash
# 1. Sauvegarder la base Temporal (elle contient tous les workflows en cours)
kubectl -n choregos-data create job --from=cronjob/temporal-pg-nightly backup-avant-montee

# 2. Jobs de schéma (l'opérateur Helm les lance, mais on vérifie)
kubectl -n choregos-temporal get jobs | grep schema

# 3. Montée progressive : history, matching, frontend, worker
helm upgrade temporal ... --set server.image.tag=<nouvelle-version>
kubectl -n choregos-temporal rollout status statefulset/temporal-history
```

Les workers Choregos supportent le rolling : le versionnage des workflows (`patched()`)
garantit qu'un worker neuf reprend un historique ancien.

## Après

```bash
temporal operator cluster health
temporal workflow list --query 'ExecutionStatus="Running"' | head
```

- Vérifier qu'aucun workflow n'est passé en `Failed` pendant la montée.
- Lancer un ticket S de bout en bout.

## Si ça tourne mal

1. Revenir à la version précédente des **services** (la base garde les historiques).
2. Si le schéma a été migré, la restauration de la base Temporal est le seul retour sûr :
   voir `restauration-postgres.md`, cluster `temporal`.
3. Les runs en cours reprendront : aucun coût n'est facturé deux fois (ADR-0008).
