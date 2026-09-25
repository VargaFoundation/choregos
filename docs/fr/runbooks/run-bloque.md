# Un run reste `Pending` ou ne finit jamais

## Reconnaître

- L'alerte `ChoregosRunEnAttente` (PipelineRun `Pending` depuis 15 min).
- Le front `/p/<projet>/runs/<id>` reste sur « en cours » sans nouvel événement.

## Distinguer trois cas

```bash
RUN=<run-id>; PROJ=<projet>
kubectl -n proj-$PROJ-runners get pipelineruns -l choregos/run-id=$RUN
kubectl -n proj-$PROJ-runners describe pipelinerun run-$RUN | tail -30
kubectl -n proj-$PROJ-runners get events --sort-by=.lastTimestamp | tail -20
```

| Ce qu'on voit | Cause | Action |
| :-- | :-- | :-- |
| `Pending`, aucun pod | pas de nœud disponible (pool `runners` à zéro, spot repris) | vérifier l'autoscaler et les taints |
| `Pending`, `FailedScheduling` : quota | `ResourceQuota` du projet atteint | attendre, ou relever le quota dans `projects/<slug>/quotas.yaml` |
| pod `Running` mais aucun événement ACP | l'agent est bloqué ou le modèle ne répond pas | regarder les logs du step `run` |

```bash
kubectl -n proj-$PROJ-runners logs -l choregos/run-id=$RUN -c step-run --tail=100
```

## Agir

1. **Le run va finir seul** dans presque tous les cas : le budget (`max_minutes`) coupe la
   session, le runner poste un résultat `failed(reason=limit)`, l'orchestrateur réessaie.
2. **Forcer l'arrêt** si c'est vraiment bloqué :
   ```bash
   choregos items action <ticket-id> stop
   ```
   L'activité `cancel_run` supprime le `PipelineRun` et son Secret.
3. **Rejouer l'étape** une fois la cause corrigée :
   ```bash
   choregos items action <ticket-id> rerun_stage
   ```

## Vérifier

- Le Secret `run-<id>` a disparu du namespace : aucun jeton ne traîne.
- La ligne de coût du run existe **une seule fois** dans `cost_ledger`.
- Le ticket a repris son cours, ou attend un humain.

## Cause récurrente à corriger

Si les `Pending` reviennent, ce n'est pas un incident mais un dimensionnement : pool
`runners` trop petit, ou `ResourceQuota` du projet trop serrée (défaut : 8 runs concurrents,
32 vCPU, 96 Go).
