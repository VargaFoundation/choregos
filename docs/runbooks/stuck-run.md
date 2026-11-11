# A run stays `Pending` or never ends

## How to know it is this

- The `ChoregosRunPending` alert (PipelineRun `Pending` for 15 min).
- The front `/p/<project>/runs/<id>` stays on "running" with no new event.

## Tell three cases apart

```bash
RUN=<run-id>; PROJ=<project>
kubectl -n proj-$PROJ-runners get pipelineruns -l choregos/run-id=$RUN
kubectl -n proj-$PROJ-runners describe pipelinerun run-$RUN | tail -30
kubectl -n proj-$PROJ-runners get events --sort-by=.lastTimestamp | tail -20
```

| What you see | Cause | Action |
| :-- | :-- | :-- |
| `Pending`, no pod | no node available (`runners` pool at zero, spot reclaimed) | check the autoscaler and the taints |
| `Pending`, `FailedScheduling`: quota | the project's `ResourceQuota` is reached | wait, or raise the quota in `projects/<slug>/quotas.yaml` |
| pod `Running` but no ACP event | the agent is stuck or the model does not answer | read the `run` step's logs |

```bash
kubectl -n proj-$PROJ-runners logs -l choregos/run-id=$RUN -c step-run --tail=100
```

With the `k8s_job` executor, a Job created **suspended** is not stuck: it is waiting for a
slot (`runner.maxActive`), and the run says so (ADR 0013).

## Act

1. **The run will end by itself** in almost every case: the budget (`max_minutes`) cuts the
   session, the runner posts a `failed(reason=limit)` result, the orchestrator retries.
2. **Force the stop** if it really is stuck:
   ```bash
   choregos items action <ticket-id> stop
   ```
   The `cancel_run` activity closes the run first — status `cancelled`, a result whose reason is
   `cancelled` — then deletes the `PipelineRun` and its Secret. The run is closed whether or not
   the delete succeeds (ADR 0143): from then on its token opens nothing but its journals.
3. **replay the stage** once the cause is fixed:
   ```bash
   choregos items action <ticket-id> rerun_stage
   ```
   This also works on a work item parked in `needs_human` whose interpreter was started again
   since it escalated (S22-41): the interpreter reads, from the work item's state changes, the
   stage that escalated and the state it left, and replays that stage with a new run number. A
   `rerun_stage` sent to such a work item before S22-41 was dropped: send it again.

## The run is closed, and something is still running

A run is over for the platform as soon as the orchestrator stops waiting for it: budget spent
(`timeout`), never admitted (`queue_timeout`), lost by the executor (`lost`), cancelled. Its row
says so before the executor is asked to remove anything. When that removal fails, the orchestrator
logs it and goes on:

```bash
kubectl -n choregos-system logs -l app.kubernetes.io/name=choregos-orchestrator --tail=-1 | grep run.cancel_failed
```

Each line names a `run_id` whose pod, Job or claim may still exist. That process can no longer
post a result, call a tool, take a git token or read its input (409 `run_closed`); what it still
does shows in the run's journal and in the API's log (`run.closed_refused`). Remove what is left:

```bash
kubectl -n proj-$PROJ-runners delete job,pipelinerun -l choregos/run-id=$RUN
```

A git token it took before the run was closed lives up to one hour: if the run's branch moved
after its run ended, compare the branch with what the run's result says it pushed before merging.

## Check it is fixed

- The `run-<id>` Secret is gone from the namespace: no token lingers.
- The run's cost line exists **exactly once** in `cost_ledger`.
- The ticket resumed its course, or is waiting for a human.

## Recurring cause to fix

If `Pending`s keep coming back it is not an incident but sizing: the `runners` pool is too
small, or the project's `ResourceQuota` too tight (default: 8 concurrent runs, 32 vCPU,
96 GB).
