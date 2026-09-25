# Upgrading Temporal

## Before

1. Read the release notes: Temporal upgrades are incremental, a major version is never
   skipped.
2. **`numHistoryShards` does not change.** It has been fixed at 512 since day one.
   Changing it invalidates the cluster.
3. Check that the replay CI is green: `uv run pytest tests/replay -q`.

## During

```bash
# 1. Back up the Temporal database (it holds every workflow in flight) — see temporal-backup.md
kubectl -n choregos-data create job --from=cronjob/temporal-pg-nightly backup-before-upgrade

# 2. Schema jobs (the Helm operator runs them, but we check)
kubectl -n choregos-temporal get jobs | grep schema

# 3. Progressive upgrade: history, matching, frontend, worker
helm upgrade temporal ... --set server.image.tag=<new-version>
kubectl -n choregos-temporal rollout status statefulset/temporal-history
```

Choregos workers support rolling upgrades: workflow versioning (`patched()`) guarantees
that a new worker resumes an old history.

## After

```bash
temporal operator cluster health
temporal workflow list --query 'ExecutionStatus="Running"' | head
```

- Check that no workflow went `Failed` during the upgrade.
- Run an S ticket end to end.

## If it goes wrong

1. Roll the **services** back to the previous version (the database keeps the histories).
2. If the schema was migrated, restoring the Temporal database is the only safe way back:
   see `restauration-postgres.md`, cluster `temporal`.
3. Runs in flight resume: no cost is billed twice (ADR-0008).
