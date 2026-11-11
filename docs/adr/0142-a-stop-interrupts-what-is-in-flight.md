# 0142 — What is running is one list, and a stop interrupts what is in flight

- **Status**: proposed, 2026-11-11
- **Concerns**: `POST /work-items/{id}/actions` (`stop`), `services/work_item_controls.apply_control`, the
  interpreter's `control` signal and stage loop (`workflows/interpreter.py`,
  `interpreter_parts/stages.py`), the `cancel_run` and `await_run` activities
  (`activities/execution.py`), `Agent.status` and `services/agents.resolve` (a suspended agent starts no
  run), `PATCH /orgs/{org}/agents/{slug}`, agent sessions (`closeAgentSession`), conversation turns
  (`cancel_turn`); builds on 0008 (idempotence), 0047 (a budget is a reservation), 0080 (hosted agents),
  0102 (a tracker comment carries no decision), 0134 (durable conversations)

## Context

An operator cannot answer "what is running right now, and for whom", nor stop it:

- **No list.** Runs in `queued` or `running` are found work item by work item. A hosted session, a
  conversation turn in flight and a run are three screens, when there is one.
- **`stop` does not stop the run.** The work item's `stop` sets the interpreter's `stopped` flag, which
  the stage loop reads **between** stages (`while ... not self.stopped`). The run in flight goes on until
  it ends, spending, writing to the repository and calling tools: a stop pressed because an agent is doing
  something wrong takes effect after it has finished doing it.
- **A run cannot be stopped alone.** `cancel_run` exists as an activity (it cancels the executor, archives
  the logs, closes the key and books the spend once), but only the evals' matrix calls it.
- **An agent cannot be stopped.** Its status can be set to `suspended` (`PATCH`), which refuses new runs
  (`resolve`: "agent is suspended: the run does not start"), and the budget sets it on its own (S29-04);
  its runs in flight carry on.

## Decision

1. **One list of what is in flight.** `GET /orgs/{org}/activity` returns runs `queued` or `running`, open
   agent sessions and conversation turns in flight, newest first, filtered by `project`, `workflow`,
   `agent`, `assistant` and `kind` (`run`, `session`, `turn`). Each entry says what it is (work item key
   and stage, or session or conversation), who started it, since when, its status, its agent and model,
   its tokens and cost so far (0141 §2), and what can be done to it. The same entries come as a stream
   (`/orgs/{org}/activity/stream`, SSE) from the events the platform already emits (`run.started`,
   `run.progress`, `run.finished`, session events). Recently finished entries (the last hour, at most
   50) follow, so a stop is seen to have taken effect. Each entry is visible only to whoever may read the
   project, the agent's session or, for a turn, the conversation's owner and an assistant's auditors.
2. **A run can be stopped alone.** `POST /runs/{id}/cancel` with a required `reason` (1-500 characters):
   - the API writes the run's result as `cancelled` with the reason and who asked, if no result is
     written yet (first writer wins: a run that has just finished keeps how it finished);
   - it starts `cancel_run` (executor cancelled, logs archived, key closed, spend booked once; replayable);
   - `await_run` returns the written result at its next poll, at most `heartbeat_seconds` later;
   - the interpreter takes a `cancelled` stage to a human: it does not retry it and does not count it as
     an attempt; the work item waits with "run stopped by <who>: <reason>", from which a person reruns the
     stage (`rerun_stage`), stops the work item or moves it on.
   Right: `item:control` on the project, as for the work item's own actions.
3. **Stopping a work item stops its run.** `stop` sets the flag as today **and** cancels the current run
   as in §2, with the same reason. `pause` does not touch the run in flight (it holds the next stage, as
   today).
4. **Stopping an agent suspends it and stops what it is doing.** `POST /orgs/{org}/agents/{slug}/stop`
   with a required `reason`:
   - the agent becomes `suspended` first, so that nothing new starts while the rest is stopped;
   - every run of that agent in `queued` or `running`, in every project of the organisation, is cancelled
     as in §2; its work items wait for a person, they are not stopped;
   - its open sessions are closed (`close_session`, reason `agent_stopped`), its conversation turns in
     flight are cancelled (`cancel_turn`);
   - the answer counts what was stopped, and what could not be (a run whose executor did not answer is
     marked and retried by the reconciliation).
   Resuming is the existing `PATCH ... {status: "active"}`; nothing stopped is restarted on its own.
   Right: `Action.MANAGE` on the agent (`agent_governance.decide`), the decision that already guards a
   status change in `PATCH /orgs/{org}/agents/{slug}`.
5. **Every stop is audited, with its reason.** `run.cancel`, `workitem.stop` (unchanged),
   `agent.stop`, each with the reason and the counts; a stop from the tracker remains refused (0102).
6. **Idempotent.** Cancelling a run that is already cancelled or ended answers `409` with how it ended and
   changes nothing; stopping a suspended agent stops what is still in flight and audits again; the
   workflow's history replays (`tests/replay/`), the new interpreter path is behind `workflow.patched`.

## Consequences

- An operator sees, on one screen, what is running in the organisation or in one workflow, for one agent
  or one assistant, with what it has spent so far, and stops a run, a work item or an agent with a reason.
- A stop takes effect within one poll of the run, not at the end of the stage.
- A cancelled run's spend is booked like any other; the work item's budget counts it.
- Not covered: stopping a single tool call inside a run (the run is the unit); a "stop everything in this
  project" button (the project's `suspend` exists); stopping an agent in another organisation that pins
  it; restarting automatically after a resume.
