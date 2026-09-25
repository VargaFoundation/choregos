# A train is frozen and nothing departs

## How to know it is this

- The `ChoregosTrainGele` alert fires (frozen for more than 2 h).
- The front `/p/<project>/trains` shows **Train frozen** with a reason.
- Tickets pile up in `pending_items` with no departure.

## Understand

A train freezes in two ways: **a human** froze it (the reason is shown), or a **rollback**
froze it automatically (`freeze_on_rollback: true`). The second case is the most frequent
and the most important: the platform refuses to redeploy by reflex after an incident.

```bash
choregos trains status <project> --env prod
# or straight from the workflow:
temporal workflow query --workflow-id train-<project>-prod --name status_query
```

## Act

1. **Read the reason.** If it comes from a rollback, find the release:
   ```bash
   choregos trains status <project> --env prod
   curl -s "$API/api/v1/projects/<project>/releases?env=prod" | jq '.items[0]'
   ```
   `verdict.reason` says what failed (canary analysis, smoke, SLO).
2. **Fix the cause**, not the symptom. A `critical` finding was created automatically: it
   carries the evidence.
3. **Unfreeze** once the fix is on its way:
   ```bash
   choregos trains unfreeze <project> --env prod
   ```
4. If a fix has to leave right now, use the **express lane**: put the `hotfix` label on the
   ticket. It shortens the soak and skips the cron — but **not** the freeze: unfreeze first.
   That is deliberate.

## Check it is fixed

- `choregos trains status <project> --env prod`: `frozen: false`, a `next_departure`.
- The batch departs: the release goes `departing` → `staging` → `done`.
- The alert resolves by itself.

## Do not

- Unfreeze without having understood the rollback: the train will leave on the same cause.
- Deploy by hand to "unblock": the third lock (Argo windows, GitHub Environment) will stop
  you, and that is a good thing.
