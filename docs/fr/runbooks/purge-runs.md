# La base grossit : purger runs, événements et transcripts

## Reconnaître

La table `run_events` domine la taille de la base, ou l'object store dépasse son quota.

```bash
kubectl -n choregos-data exec -it choregos-pg-1 -- psql choregos -c "
  select relname, pg_size_pretty(pg_total_relation_size(relid)) as taille
  from pg_catalog.pg_statio_user_tables order by pg_total_relation_size(relid) desc limit 10"
```

## Ce qu'on garde, et pourquoi

| Donnée | Rétention | Raison |
| :-- | :-- | :-- |
| `runs` (métadonnées, résultat) | indéfinie | l'historique d'un ticket doit rester lisible |
| `run_events` (journal ACP) | 90 jours | volumineux ; le transcript archivé reste |
| `events` (bus interne) | 180 jours | audit et statistiques |
| `cost_ledger` | indéfinie | comptabilité |
| transcripts, rapports (object store) | 180 jours (lifecycle) | preuve d'un run |
| `audit_log` | indéfinie | exigence d'audit |

## Purger

```sql
-- Journal ACP de plus de 90 jours (les runs eux-mêmes sont conservés)
DELETE FROM run_events WHERE ts < now() - interval '90 days';

-- Bus interne de plus de 180 jours
DELETE FROM events WHERE ts < now() - interval '180 days';

-- Livraisons de webhooks (dédup) de plus de 30 jours
DELETE FROM webhook_deliveries WHERE ts < now() - interval '30 days';
```

Puis `VACUUM (ANALYZE)` sur les tables purgées.

## Ne pas purger

- `cost_ledger` et `audit_log` : ce sont les deux tables qu'on regrette toujours d'avoir purgées.
- Les runs d'un ticket encore ouvert.

## Automatiser

Un `CronJob` mensuel applique ces requêtes en staging depuis six mois ; en production, la
purge reste déclenchée à la main après lecture des volumes.
