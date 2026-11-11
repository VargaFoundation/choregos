---
title: Backups and restore
description: What to back up on a Choregos instance, and how to restore it to one consistent instant.
sidebar_position: 6
---

# Backups and restore

**What you will be able to do:** know what an instance cannot lose, back it up, and restore it.

## What to back up

| What | Why | How |
| --- | --- | --- |
| **The platform database** (PostgreSQL) | projects, work items, decisions, the journal, the cost ledger, sealed secrets | continuous archiving with point-in-time recovery; with CloudNativePG, a target of 5 minutes of data at most |
| **Temporal's database** | where each work item in flight stands | the same tool and the same frequency as the platform database |
| **The object store** | the file artefacts of runs (documents, spreadsheets, images) | your object store's versioning or replication |
| **The `secrets-keks` key** | without it, every connector secret in the database is unreadable | in your vault, with the vault's own backups; keep old keys while a backup sealed with them can still be restored |
| **Your Helm values and Secrets' source** | to rebuild the instance | in git and in your vault |

**Restore the platform database and Temporal's to the same instant.** A work item whose position in
Temporal and in the platform database disagree is stuck or replays a step.

## Restore

1. Restore PostgreSQL into a **new** cluster at the chosen instant, then point
   `global.database.secretRef` at it:
   [PostgreSQL restore runbook](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/postgres-restore.md).
2. Restore Temporal's database to the same instant:
   [Temporal backup runbook](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/temporal-backup.md).
3. Restart the API and the workers, then list the workflows Temporal runs and compare them with the
   work items the console shows in progress. The Temporal runbook says how to start again a work item
   that lost its workflow.

## Purging

The database grows with runs, events and transcripts; the
[purge runbook](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/purge-runs.md)
says what can go, and keeps what the audit needs.
