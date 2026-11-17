# Single node: restore from the nightly backup

For an installation made with `charts/choregos/values/single-node.yaml` ([ADR 0184](../adr/0184-a-supported-single-node-installation.md)).
What the backup holds: every database of the embedded PostgreSQL (`pg_dump -Fc --create`), Temporal's
SQLite file (an online copy), the memory's files, and the object store when `backup.objectStore` is on.
What it does not hold: the site's secrets (`choregos-api-secrets`, `choregos-oidc`, the object store's
and the backup target's keys). Those come from the site's vault.

## How to know it is this

- The machine is lost (disk, host, accidental `k3s-uninstall.sh`): nothing answers, or a new machine
  is ready and empty.
- Or the data is wrong and must go back to last night: a bad migration, an erasure by mistake.
  Everything since the backup is lost by the restore (the recovery point is the last backup, 24 h at
  most with the default schedule). Say so to the people who used it today before you start.

## Which backup

```bash
# The backups, newest last (S3 target; with a volume target: `ls` the volume).
rclone lsf --dirs-only :s3,provider=Other,endpoint=$S3_ENDPOINT:$BUCKET/$PREFIX
```

Only a directory holding `COMPLETE` is a backup; its `MANIFEST` lists the files and their sizes.
Without a stamp the restore takes the newest complete one.

## Restore onto a new machine

1. **k3s** on the new machine, the same version or newer:
   `curl -sfL https://get.k3s.io | sh -`, then `kubectl get storageclass local-path`.
2. **The site's secrets**, from the vault, with the SAME values as before. The run-token key and
   `secrets-keks` above all: with another `secrets-keks` no sealed connector secret opens again.

   ```bash
   kubectl create namespace choregos
   kubectl -n choregos create secret generic choregos-api-secrets \
     --from-file=run-token-private-key=run-token.pem \
     --from-literal=session-secret="$SESSION_SECRET" --from-literal=secrets-keks="$SECRETS_KEKS"
   kubectl -n choregos create secret generic choregos-oidc --from-literal=client-secret="$OIDC_SECRET"
   kubectl -n choregos create secret generic choregos-backup-target \
     --from-literal=access-key="$S3_ACCESS_KEY" --from-literal=secret-key="$S3_SECRET_KEY"
   ```
3. **The chart**, with the same values files as the lost installation:
   `helm install choregos charts/choregos -n choregos -f charts/choregos/values/single-node.yaml -f my-site.yaml`.
   Wait until every Deployment is available (`kubectl -n choregos get deploy`). It starts empty; the
   restore replaces that.
4. **The restore**:

   ```bash
   tools/single-node/restore.sh                    # the newest complete backup
   tools/single-node/restore.sh 20261117T020000Z   # or that one
   ```

   It stops every writer (API, console, workers, Temporal, gateway, memory), runs the chart's restore
   job (`kubectl create job --from=cronjob/choregos-restore …`), prints its log, and starts the
   writers again with the replica counts they had. The job drops and re-creates each database from its
   dump, puts Temporal's and the memory's files back, and copies the object store back when the
   backup holds it.

## Restore in place (same machine, data back to last night)

Skip steps 1 to 3: `tools/single-node/restore.sh [stamp]` on the running installation.

## If the restore fails

The script leaves the writers stopped: starting them over a half-restored database would write on
it. Read the job's log (`kubectl -n choregos logs job/<name> -c fetch|database|files|objects`):

- `no complete backup … nothing restored`: the bucket, prefix or keys of `backup.target` are not
  those of the backups; check `choregos-backup-target` and the values.
- `pg_restore` errors: the database step stops at the first error (`--exit-on-error`); a dump made
  by a newer PostgreSQL than the embedded one is refused. Restore with the chart version that made
  the backup.

Fix the cause and run the script again: every step overwrites what the previous attempt left.

## How to check it is fixed

```bash
kubectl -n choregos get deploy                      # every Deployment available
kubectl -n choregos logs deploy/choregos-api | grep -i "API starting"
```

Then in the console: the work items of yesterday are there, in the state they had at the backup. A
work item whose workflow was running resumes from where Temporal's copy left it; one created after the
backup is gone (its tracker issue, if any, is picked up again by the tracker catch-up).

## How it is proven

`tools/single-node/proof.sh` plays this whole runbook from a blank node: install, one work item to
`approved`, backup, the node destroyed, a new node, install, `restore.sh`, the work item read back
`approved`. See `docs/plan/STATUS.md` (S80-07b) for when it last ran and what it found.
