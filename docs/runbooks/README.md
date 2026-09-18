# Runbooks

Un runbook se lit à 3 h du matin, par quelqu'un qui n'a pas écrit le code. Chacun commence
par *comment savoir que c'est ça*, puis donne les commandes, puis dit *comment vérifier que
c'est réglé*.

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
