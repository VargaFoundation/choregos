# Restoring PostgreSQL (PITR)

## How to know it is this

Data loss, corruption, or an accidental deletion. Target RPO: 5 minutes. Target RTO: 30
minutes.

## Before restoring

1. **Stop the writes**: scale the API to zero replicas and pause the workers.
   ```bash
   kubectl -n choregos-system scale deploy/choregos-api --replicas=0
   kubectl -n choregos-system scale deploy/choregos-orchestrator-orchestrator --replicas=0
   ```
2. **Note the restore instant** (just before the incident), in UTC.
3. **Check that a backup covers that instant**:
   ```bash
   kubectl -n choregos-data get backups.postgresql.cnpg.io
   ```

## Restore

CloudNativePG restores into a **new cluster**; we never restore over the existing one.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: choregos-pg-restore
  namespace: choregos-data
spec:
  instances: 3
  bootstrap:
    recovery:
      source: choregos-pg
      recoveryTarget:
        targetTime: "2026-09-19 02:15:00+00"   # the instant noted above
  externalClusters:
    - name: choregos-pg
      barmanObjectStore:
        destinationPath: s3://choregos-backups/choregos-pg
        # `serverName` is the name of the ORIGINAL cluster. Without it, CNPG looks for the
        # backup under the new cluster's name and answers "no target backup found", with
        # an object store that is nonetheless full. Verified by restoring for real.
        serverName: choregos-pg
        s3Credentials:
          accessKeyId: { name: choregos-backup, key: access-key-id }
          secretAccessKey: { name: choregos-backup, key: secret-access-key }
```

```bash
kubectl apply -f restore.yaml
kubectl -n choregos-data wait --for=condition=Ready cluster/choregos-pg-restore --timeout=30m
```

## Switch over

1. Check the restored content **before** switching:
   ```bash
   kubectl -n choregos-data exec -it choregos-pg-restore-1 -- psql choregos -c \
     "select count(*), max(created_at) from work_items"
   ```
2. Point the `choregos-db` secret at the new service (`choregos-pg-restore-rw`).
3. Bring the API and the workers back, in that order.

## Check it is fixed

- `/readyz` answers, the front shows the projects.
- Temporal workflows resume: their state is in Temporal, not in this database — restore
  both to the same instant (`temporal-backup.md`).
- **Beware**: runs in flight at the restore point will be replayed; idempotence (ADR-0008)
  avoids the double cost.

## Monthly test

A verification `CronJob` restores in staging and compares the counts. A failure of this test
is an incident: an untested backup is not a backup.
