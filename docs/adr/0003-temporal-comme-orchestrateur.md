# ADR-0003 — Temporal pour l'orchestration durable

- **État** : acceptée
- **Concerne** : S1, S9, S7

## Contexte

Un ticket vit des heures ou des jours : un agent travaille, un humain valide, la CI tourne,
un train part. Il faut survivre aux redémarrages, aux pannes de nœud, aux déploiements de
la plateforme elle-même — sans perdre l'état ni payer deux fois le même run.

## Décision

Temporal, auto-hébergé (Helm + CloudNativePG), avec la possibilité de passer à Temporal
Cloud sans changer une ligne de code. Un workflow par ticket (`WorkflowInterpreter`), un
par environnement (`ReleaseTrain`), un par projet (`FindingsTriage`, `MemoryIngestion`),
un par provisioning.

Trois disciplines rendent cela sûr :

- **la logique de décision est pure** : `choregos_core.WorkflowEngine` ne fait aucune
  entrée/sortie, ce qui la rend testable sans Temporal et rejouable sans surprise ;
- **les activités sont idempotentes** par `(run_id | work_item_id, étape)` ;
- **`workflow.patched()` est obligatoire** pour tout changement de logique, et la CI
  rejoue des historiques archivés (`tests/replay`).

`numHistoryShards: 512` est figé dès le départ : il n'est pas modifiable ensuite.

## Conséquences

- Une panne de worker ne perd rien et ne facture rien deux fois.
- L'opérateur voit l'état réel (requêtes de workflow) plutôt qu'un reflet en base.
- On accepte un composant de plus à exploiter, avec ses sauvegardes et ses montées de version.

## Alternatives écartées

- **File + base d'état maison** : réécrire mal ce que Temporal fait bien.
- **Airflow / Argo Workflows** : pensés pour des DAG de traitement, pas pour des processus
  longs qui attendent des humains.
