# Runbooks

A runbook is read at 3 a.m. by someone who did not write the code. Each one starts with
*how to know it is this*, then gives the commands, then says *how to check it is fixed*.

> The runbooks are in French (written before 2026-09-24); they are translated as they are
> played in staging (STATUS S7-10). English summary of when to open which:

| Runbook | When |
| :-- | :-- |
| `train-gele.md` | a train is frozen and nothing departs |
| `api-5xx.md` | the API returns 5xx |
| `restauration-postgres.md` | data loss, PITR restore — the only one played on a cluster so far |
| `ecphoria-quorum.md` | Ecphoria lost its Raft quorum |
| `rotation-secrets.md` | rotating the GitHub App, the gateway `master_key`, the JWT keys |
| `montee-temporal.md` | upgrading Temporal |
| `purge-runs.md` | the database grows, transcripts pile up |
| `run-bloque.md` | a run stays `Pending` or never ends |
| `webhook-perdu.md` | an `agent-ready` ticket did not start |
| `perte-de-noeud.md` | a node disappears (spot eviction, failure) and work does not resume |
| `aca-live.md` | checking the Azure Container Apps executor against a real subscription |

## Index (français)

| Runbook | Quand |
| :-- | :-- |
| [train-gele.md](train-gele.md) | un train est gelé et rien ne part |
| [api-5xx.md](api-5xx.md) | l'API renvoie des 5xx |
| [restauration-postgres.md](restauration-postgres.md) | perte de données, restauration PITR |
| [ecphoria-quorum.md](ecphoria-quorum.md) | Ecphoria a perdu son quorum Raft |
| [rotation-secrets.md](rotation-secrets.md) | rotation d'App GitHub, `master_key`, clés JWT |
| [montee-temporal.md](montee-temporal.md) | montée de version de Temporal |
| [purge-runs.md](purge-runs.md) | la base grossit, les transcripts s'accumulent |
| [run-bloque.md](run-bloque.md) | un run reste `Pending` ou ne finit jamais |
| [webhook-perdu.md](webhook-perdu.md) | un ticket `agent-ready` n'a pas démarré |
| [perte-de-noeud.md](perte-de-noeud.md) | un nœud disparaît (éviction spot, panne) et le travail ne repart pas |
| [aca-live.md](aca-live.md) | vérifier l'exécuteur Azure Container Apps contre un vrai abonnement |
