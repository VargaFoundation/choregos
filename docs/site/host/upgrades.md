---
title: Upgrades
description: Upgrade a Choregos instance with Helm, in the right order, with its migrations, and know what to do when it goes wrong.
sidebar_position: 5
---

# Upgrades

**What you will be able to do:** move an instance to a new version without losing work in flight.

## Before you upgrade

1. **Read the release notes** of every version between yours and the target, on the
   [GitHub releases](https://github.com/VargaFoundation/choregos/releases), and the "Upgrading to …"
   sections of the
   [deployment reference](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/deployment.md#upgrading-to-024).
   A breaking change says what to rename or set before you upgrade.
2. **Back up** the platform database and Temporal's, to the same instant ([Backups](backups.md)).
   The migrations change the schema; restoring a backup is the only way back.
3. Upgrade one minor version at a time when the notes say so.

## Upgrade

The same command as the install, with the new version:

```bash
helm upgrade --install choregos oci://ghcr.io/vargafoundation/charts/choregos \
  --version 0.25.3 --namespace choregos -f my-values.yaml
```

- The database migrations run as a Helm hook **before** the new API starts
  (`python -m choregos_api.migrate`, in a Job that waits for the database up to 300 seconds). With an
  embedded database the hook runs after the install instead: keep `choregos-api.migrations.hook` as
  the chart sets it.
- Work items in flight finish on the workflow version they started on; Temporal keeps their position
  across the restart of the workers.
- The images follow the chart's version.

## Check it

```bash
kubectl -n choregos get pods
kubectl -n choregos get jobs -l app.kubernetes.io/instance=choregos
curl -s https://choregos.example.com/api/v1/edition
```

**What you should see:** every pod `Running` on the new images, the job `choregos-migrations-<revision>`
completed, and the edition answer's `version` naming the new version.

## When it goes wrong

| Symptom | What to do |
| --- | --- |
| the migration Job fails | read its logs; the API did not start on the new version, the old pods still serve. Fix the cause, then run the upgrade again |
| the API refuses to start after the upgrade | its log names the setting; most often a value the release notes asked to change |
| you must go back | `helm rollback` restores the previous pods, not the previous schema: restore the database backup taken before the upgrade, then roll back |

Upgrading Temporal itself has its own
[runbook](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/temporal-upgrade.md).
