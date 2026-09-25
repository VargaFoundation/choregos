# A run stays `Pending` or never ends

## How to know it is this

- The `ChoregosRunEnAttente` alert (PipelineRun `Pending` for 15 min).
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
   The `cancel_run` activity deletes the `PipelineRun` and its Secret.
3. **Replay the stage** once the cause is fixed:
   ```bash
   choregos items action <ticket-id> rerun_stage
   ```

## Check it is fixed

- The `run-<id>` Secret is gone from the namespace: no token lingers.
- The run's cost line exists **exactly once** in `cost_ledger`.
- The ticket resumed its course, or is waiting for a human.

## Recurring cause to fix

If `Pending`s keep coming back it is not an incident but sizing: the `runners` pool is too
small, or the project's `ResourceQuota` too tight (default: 8 concurrent runs, 32 vCPU,
96 GB).
