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
   [deployment reference](../../deployment.md#upgrading-to-024).
   A breaking change says what to rename or set before you upgrade.
2. **Back up** the platform database and Temporal's, to the same instant ([Backups](backups.md)).
   The migrations change the schema; restoring a backup is the only way back.
3. Upgrade one minor version at a time when the notes say so.

## Upgrade

The same command as the install, with the new version:

```shell
helm upgrade --install choregos oci://ghcr.io/vargafoundation/charts/choregos \
  --version 0.26.2 --namespace choregos -f my-values.yaml
```

- The database migrations run as a Helm hook **before** the new API starts
  (`python -m choregos_api.migrate`, in a Job that waits for the database up to 300 seconds). With an
  embedded database the hook runs after the install instead: keep `choregos-api.migrations.hook` as
  the chart sets it.
- Work items in flight finish on the workflow version they started on; Temporal keeps their position
  across the restart of the workers.
- The images follow the chart's version.

## Check it

```shell
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
[runbook](../../runbooks/temporal-upgrade.md).

## Upgrading to 0.26

0.26 brings [assistants](../use/assistants.md): a plugin may ship one, an organisation installs it for
an audience, and people converse with it in the console, from an MCP client or in an embedded frame.
Nothing changes for an organisation that installs none. The migrations run with the chart's job as
usual. The full table is in the
[deployment reference](../../deployment.md#upgrading-to-026); what you do is:

1. **Bound the token exchange.** Set `CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` to a JSON list of your sources'
   audiences (in `global.extraEnv`) before binding a need that uses the person's identity
   (`user_exchange`). Unset, a connector's editor chooses any audience the identity provider allows. See
   the [review of the person's identity](../../security.md).
2. **Prepare the identity provider.** `user_exchange` needs token exchange (RFC 8693) enabled for the
   API's client and limited to those audiences. A standing delegation needs `offline_access` with
   bounded, revocable offline sessions. See [Identity](identity.md).
3. **Give conversation suites a model.** An assistant's conversation suites gate the promotion of its
   agent version, and a judge model scores their rubrics (`conversation_judge_model`, else the `strong`
   model profile). With no gateway key, a version whose assistant ships suites is refused promotion
   with the reason. `conversation_suites_require_model=false` opts out, and no check then runs.
4. **Pick a quiet hour.** The migration that makes `/responses` sessions durable closes the ones open at
   upgrade time. Matters only if you use hosted prompt agents.
5. **List the host origins** of an embedded assistant in the installation's `config.embed_origins`; nothing
   else to deploy. Accounts connected before 0.26 are connected again once.

:::caution Delegated tasks
The standing delegation ships, but its decision record
([ADR 0137](../../adr/0137-a-standing-read-only-delegation.md)) stays proposed until the works council has
given its opinion and the data protection impact assessment is done. Do not offer delegated tasks to
people before then.
:::
