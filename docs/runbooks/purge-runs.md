# The database grows: purging runs, events and transcripts

## How to know it is this

The `run_events` table dominates the database size, or the object store exceeds its quota.

```bash
kubectl -n choregos-data exec -it choregos-pg-1 -- psql choregos -c "
  select relname, pg_size_pretty(pg_total_relation_size(relid)) as size
  from pg_catalog.pg_statio_user_tables order by pg_total_relation_size(relid) desc limit 10"
```

## What we keep, and why

| Data | Retention | Reason |
| :-- | :-- | :-- |
| `runs` (metadata, result) | indefinite | a ticket's history must stay readable |
| `run_events` (ACP journal) | 90 days | large; the archived transcript remains |
| `events` (internal bus) | 180 days | audit and statistics |
| `cost_ledger` | indefinite | accounting |
| transcripts, reports (object store) | 180 days (lifecycle) | evidence of a run |
| `audit_log` | indefinite | audit requirement |

## Purge

```sql
-- ACP journal older than 90 days (the runs themselves are kept)
DELETE FROM run_events WHERE ts < now() - interval '90 days';

-- Internal bus older than 180 days
DELETE FROM events WHERE ts < now() - interval '180 days';

-- Webhook deliveries (dedup) older than 30 days
DELETE FROM webhook_deliveries WHERE ts < now() - interval '30 days';
```

Then `VACUUM (ANALYZE)` on the purged tables.

## Do not purge

- `cost_ledger` and `audit_log`: the two tables one always regrets having purged.
- The runs of a ticket that is still open.

## Automate

A monthly `CronJob` has applied these queries in staging for six months; in production the
purge is still triggered by hand after reading the volumes.
