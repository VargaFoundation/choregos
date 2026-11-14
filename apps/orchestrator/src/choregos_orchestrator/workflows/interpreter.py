# SPDX-License-Identifier: Apache-2.0
"""`WorkflowInterpreter`: runs the pinned DSL for a ticket (docs/plan/02 §2.1).

The workflow does **no** input/output and **never** calls a model: it decides, the activities act.
All the decision logic comes from `choregos_core.WorkflowEngine`, which makes it testable without
Temporal and replayable without surprises.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter, deque
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from .scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import Workflow
    from choregos_core import WorkflowEngine

    from ..activities import tracker as tracker_activities
    from ..activities.interpretation import (
        activity_of,
        load_context,
        message_of,
        record_workflow_failure,
    )

#: `migrate` receives the target definition in the signal, records it and no longer kills the ticket
#: (ADR 0031, S16-05). Histories from before this marker replay the old path.
MIGRATION_BY_DEFINITION = "migration-par-definition"  # lang: fr-ok — a patch id is never renamed
#: A system transition proposes a governed action, at a date (S20-05). Histories from before this
#: marker had none: their system transitions only know guarantees.
TRANSITION_ACTIONS = "actions-de-transition"  # lang: fr-ok — a patch id is never renamed
#: A human transition can be a TASK: a form, an attestation (S20-06). Before this marker, every
#: human transition asked for an approval.
HUMAN_TASKS = "taches-humaines"  # lang: fr-ok — a patch id is never renamed
#: The train brings the ticket the end of ITS departure, and the approval its workflow requires (ADR
#: 0041, S21-21). Before this marker, the ticket took any `cd.*` event as its own, whatever its
#: environment: a finished staging counted for prod.
TRAIN_PER_ENVIRONMENT = "train-par-environnement"  # lang: fr-ok — a patch id is never renamed
#: A ticket parked by a question or an escalation RESUMES (#313): an answer, or "Replay the stage",
#: brings it back to the state it left, with a fresh attempts budget. Before this marker,
#: `on_answer: resume` was declared and nothing played it: only the abandon got out of it.
RESUME_AFTER_ESCALATION = "resume-after-escalation"
#: A `done` result that lacks a declared output fails the stage, whatever the output's type (S31-02,
#: ADR 0049). Histories recorded before this marker advanced on such a result unless a gate refused.
DECLARED_OUTPUTS_REQUIRED = "declared-outputs-required"
#: With an executor that announces `suspend` and `resume` (S35-03, ADR 0016), a question
#: SUSPENDS the run's sandbox, and the answer resumes THAT run instead of replaying the stage:
#: one run, not two. Histories before this marker, and every executor without the two
#: capabilities, replay the stage as before.
SUSPEND_AROUND_HUMAN = "suspend-around-human"
#: A stage stopped on budget — the gateway refused its key, or the work item is over its budget —
#: goes to a person and is never retried (S27-01, ADR 0047). Before this marker, a `FAILED` result
#: with `reason="budget"` and no `on_budget_exceeded` was retried with a fresh key.
BUDGET_NEVER_RETRIES = "budget-never-retries"
#: The same failure on two consecutive attempts of a stage escalates instead of retrying (S27-07):
#: a third identical attempt would cost the same and fail the same.
IDENTICAL_FAILURE_ESCALATES = "identical-failure-escalates"
#: A system transition can open one child work item per element of a list and wait until every child
#: is settled (S38-12, ADR 0057). No history before this marker holds a `spawn:`.
FAN_OUT = "fan-out-and-join"

#: `prepare_stage` mints a run's key at what is left, and refuses to start a stage below the floor
#: (S27-02, ADR 0047): the stage is then sent to a person without a run. Before this marker,
#: `prepare_stage` never refused on budget.
BUDGET_RESERVATION = "budget-reservation"
#: The policy's `attempts.by_role` caps an agent transition's `max_attempts` (S27-08). Before
#: this marker it was read by no one.
POLICY_ATTEMPTS_CEILING = "policy-attempts-ceiling"
#: The ticket's spend is read from the cost ledger — models and tools — at start and after each
#: stage (S27-03, ADR 0047). Before this marker it lived in workflow memory, summed from model
#: spend only, and a restarted interpreter started again at zero.
TICKET_SPEND_FROM_LEDGER = "ticket-spend-from-ledger"
#: Once a transition's pending guarantees (CI, review) have their verdict, ALL its guarantees are
#: evaluated again before deciding (S22-32, #359): one more activity per resolution, not per poll.
#: Before this marker, the synchronous ones kept the verdict read when the transition was entered,
#: and an ADR number taken on the default branch during the CI was merged as a duplicate.
GATES_REREAD_AFTER_WAIT = "gates-reread-after-wait"
#: An escalation writes where the work item resumes — `[parked, origin, transition]` — on its
#: `state_changed` event (S22-41). Before this marker the mirror carried the state and the reason.
ESCALATION_CONTEXT_STORED = "escalation-context-stored"
#: An interpreter started again from the database (#497) did not know where its parked work item
#: resumes: `rerun_stage` and an answer were dropped (S22-41). A request to resume on a work item
#: this interpreter did not park now reads it back (`escalation_context`). The id is numbered per
#: request (`-1`, `-2`…): a request the old code dropped, replayed, must answer no without making
#: every later request answer no — `patched` gives one answer per id for the whole run.
ESCALATION_CONTEXT_AFTER_RESTART = "escalation-context-after-restart"
#: `prepare_stage` refusing a stage on its configuration — a model the resolver refuses, an agent,
#: instructions, a skill or a knowledge base it cannot use — escalates the work item like a failed
#: attempt, and the interpreter waits for the configuration to be fixed and the stage replayed
#: (#543). Before this marker the refusal failed the interpreter, and the work item had no workflow.
STAGE_CONFIG_REFUSAL_ESCALATES = "stage-config-refusal-escalates"
#: An interpreter (re)started on a work item restores each transition's attempts from its runs
#: that ended without success (#550). Before this marker it started at attempt 1: `prepare_stage`
#: handed back the run of that number, already over, `await_run` its old result, and the stage was
#: never played again.
ATTEMPTS_FROM_RUNS = "attempts-from-runs"
#: A `rerun_stage` grants ONE more attempt to the stage it replays, not a fresh allowance
#: (S27-13, ADR 0047 decision 4): `max_attempts` stays a cap, and a person's click is one more try.
#: Before this marker the click reset the stage's attempts: each click bought `max_attempts` new ones.
RERUN_GRANTS_ONE_ATTEMPT = "rerun-grants-one-attempt"
#: An answer that resumes a parked work item grants the stage ONE more attempt too, like a rerun
#: (S27-04, ADR 0047 decision 4). Before this marker an answer reset the stage's attempts, so each
#: question answered bought `max_attempts` new ones.
ANSWER_GRANTS_ONE_ATTEMPT = "answer-grants-one-attempt"
#: `open_pull_request` refusing a work item that has no branch — its implementation changed nothing,
#: so nothing was pushed — escalates the work item with that reason (S22-46, #539). Before this
#: marker GitHub answered 422 `head invalid` and the interpreter failed.
OPEN_PR_WITHOUT_BRANCH_ESCALATES = "open-pr-without-branch-escalates"
#: The executor refusing a run on the deployment's configuration at `start_run` — a policy asking for
#: a RuntimeClass the warm pool does not run (#750, S38-13e) — escalates the work item at once, like
#: `prepare_stage`'s refusals: the run is already closed `failed` with the reason, and replaying it
#: would be refused the same way until an operator fixes the configuration. Before this marker the
#: refusal failed the interpreter, and the work item had no workflow.
START_REFUSAL_ESCALATES = "start-refusal-escalates"
#: An agent step that measured no change takes its state's "already fixed" exit (S22-47, #539): the
#: work item ends there when the exit's guarantees prove the fix is already on the default branch, and
#: escalates at once otherwise. Before this marker it went on to test, review, and the pull request.
ALREADY_FIXED_ON_EVIDENCE = "already-fixed-on-evidence"
#: The transition selected after a step that recorded decisions — a decision step's answers, an
#: agent's declared confidence — is written on its ledger records (ADR 0121 §6, S52-05): one more
#: activity, `note_transition_taken`. Before this marker no ledger existed.
DECISION_LEDGER = "decision-ledger"
#: A new pass of a work item whose ticket came back (C1) numbers its runs past those of the passes
#: before it, and starts its attempts budget at zero (`attempts_before_pass`). No history before this
#: marker holds a pass after the first.
FRESH_ATTEMPTS_EACH_RECURRENCE = "fresh-attempts-each-recurrence"
#: A migration — a re-route (ADR 0123) — requested while a human step waits is played at once: the
#: pending request is closed as superseded (it judges no decision) and the loop migrates. Before this
#: marker the human wait did not see it; the API refused such a migration.
HUMAN_WAIT_YIELDS_TO_MIGRATION = "human-wait-yields-to-migration"
#: A run a person stopped (`POST /runs/{id}/cancel`, a work item's `stop`; ADR 0142 §2-3) takes its
#: stage to a person: it is not retried and does not count as an attempt, and the work item waits with
#: "run stopped by <who>: <reason>". A work item stopped meanwhile ends as a stop does. No history
#: before this marker holds such a result.
RUN_STOPPED_GOES_TO_A_PERSON = "run-stopped-goes-to-a-person"
#: The status comment is best-effort (#1646, S62-05): when the tracker does not take it once
#: `update_status_comment`'s retries are spent, the interpreter logs it and goes on. The comment only
#: mirrors the platform's state, and the next mirror rewrites it whole. Before this marker the failure
#: failed the interpreter (FON-277 stood still for two days, 2026-11-09).
STATUS_COMMENT_BEST_EFFORT = "status-comment-best-effort"


def _attempts_ceiling(context: dict[str, Any], actor: Any) -> int | None:
    """The policy's ceiling on this transition's attempts (`attempts.by_role`, S27-08); `None`
    when the policy names none for the role, for a job (no role: ADR 0056), or for a history
    recorded before `POLICY_ATTEMPTS_CEILING`."""
    ceilings = context.get("attempts_ceilings")
    if not ceilings or actor is None or not workflow.patched(POLICY_ATTEMPTS_CEILING):
        return None
    ceiling = ceilings.get(str(actor.role))
    return int(ceiling) if ceiling else None


# The helpers and the mixins read the patch ids above: imported once they exist.
from .interpreter_parts.external import ExternalMixin  # noqa: E402
from .interpreter_parts.gates import GatesMixin  # noqa: E402
from .interpreter_parts.helpers import (  # noqa: E402, F401
    _MIGRATION,
    ACTION_REREAD,
    CHILDREN_REREAD,
    CONFIGURATION_REFUSALS,
    DEFAULT_RETRY,
    FILE_MAX_MINUTES,
    HISTORY_THRESHOLD,
    NO_RETRY,
    POLL_RETRY,
    InterpreterInput,
    InterpreterStatus,
    Refusal,
    _decision,
    _for_this_environment,
    wait_signal,
)
from .interpreter_parts.humans import HumansMixin  # noqa: E402
from .interpreter_parts.refusals import RefusalsMixin  # noqa: E402
from .interpreter_parts.resume import ResumeMixin  # noqa: E402
from .interpreter_parts.stages import StagesMixin  # noqa: E402
from .interpreter_parts.suspension import SuspensionMixin  # noqa: E402
from .interpreter_parts.system import SystemMixin  # noqa: E402
from .interpreter_parts.trains import TrainsMixin  # noqa: E402

#: Re-exported for the modules that import them from here (`workflows/trial.py`, tests).
__all__ = [
    "ACTION_REREAD",
    "CHILDREN_REREAD",
    "CONFIGURATION_REFUSALS",
    "DEFAULT_RETRY",
    "FILE_MAX_MINUTES",
    "HISTORY_THRESHOLD",
    "NO_RETRY",
    "POLL_RETRY",
    "InterpreterInput",
    "InterpreterStatus",
    "Refusal",
    "WorkflowInterpreter",
    "wait_signal",
]


@workflow.defn(name="WorkflowInterpreter", sandboxed=False)
class WorkflowInterpreter(
    ExternalMixin,
    GatesMixin,
    HumansMixin,
    RefusalsMixin,
    ResumeMixin,
    StagesMixin,
    SuspensionMixin,
    SystemMixin,
    TrainsMixin,
):
    """One workflow per ticket, deterministic ID `wi-<project>-<tracker_key>`."""

    def __init__(self) -> None:
        self.inbox: deque[dict[str, Any]] = deque()
        self.decisions: deque[dict[str, Any]] = deque()
        self.attempts: Counter[str] = Counter()
        self.state: str = ""
        self.cost_usd: float = 0.0
        self.paused: bool = False
        self.stopped: bool = False
        self.current_run: str | None = None
        self.last_run: str | None = None
        self.pending_request: str | None = None
        self.last_outcome: str | None = None
        #: The outputs of the work item's last stage: a transition's `when` chooses on them (S48-01).
        self.last_outputs: dict[str, Any] | None = None
        self.workflow_override: dict[str, Any] | None = None
        self.state_mapping: dict[str, str] = {}
        #: The whole `migrate` signal: the definition, its identifier, the mapping.
        self.migration: dict[str, Any] = {}
        self.migrations: int = 0
        self.findings_count: int = 0
        #: The ticket's fields, which an action date reads; `fields_changed` replaces them.
        self.fields: dict[str, Any] = {}
        self.fields_version: int = 0
        #: The settled actions their `action_settled` announced, by identifier.
        self.settled_actions: dict[str, str] = {}
        #: What the ticket is waiting for: an action's date, the action itself.
        self.waiting: str | None = None
        self.current_action: str | None = None
        #: The failure signature of each stage's last attempt (S27-07).
        self.failure_signatures: dict[str, str] = {}
        #: The parked ticket (question, escalation) and where it will resume: `(parked, origin, transition)`.
        self.resume_point: tuple[str, str, str] | None = None
        #: The attempts from before the last resumption: a run number is not reused (it makes up
        #: the run's identifier), while the attempts budget starts again from zero.
        self.attempt_resets: Counter[str] = Counter()
        #: "Replay the stage" requested (`rerun_stage`), not yet played.
        self.rerun_requested: bool = False
        #: The policy's `attempts.by_role` ceilings, read with the context: a rerun needs them (S27-13).
        self.ceilings: dict[str, Any] = {}
        #: The run suspended across a person's answer (S35-03): `{run_id, key, until, answered}`.
        self.suspension: dict[str, Any] | None = None
        #: How many suspended runs were resumed: names each resumption, for idempotence.
        self.resumptions: int = 0
        #: A request to resume (`rerun_stage`, an answer) on a work item this interpreter did not
        #: park: where it resumes is read from the database before the request is played (S22-41).
        self.resume_lookup_pending: bool = False
        #: How many such requests arrived: numbers the patch id of each (see the marker).
        self.resume_lookups: int = 0
        self.project_id: str = ""
        self.work_item_id: str = ""
        #: How many `child_settled` signals arrived: a join re-reads its children when it moves.
        self.children_settled: int = 0
        #: The run whose ledger records wait for the transition selected next (ADR 0121 §6).
        self.ledger_run: str | None = None

    # ───────────────────────── signals and queries ─────────────────────────

    @workflow.signal
    def human_decision(self, decision: dict[str, Any]) -> None:
        self.decisions.append(decision)
        if decision.get("kind") == "question":
            self._look_up_resume()

    @workflow.signal
    def inbound(self, event: dict[str, Any]) -> None:
        self.inbox.append(event)

    @workflow.signal
    def question_asked(self, payload: dict[str, Any]) -> None:
        self.pending_request = str(payload.get("request_id") or "")

    @workflow.signal
    def finding(self, payload: dict[str, Any]) -> None:
        self.findings_count += 1

    @workflow.signal
    def fields_changed(self, payload: dict[str, Any]) -> None:
        """The fields changed (S20-05): an action date is recomputed, its timer is re-armed."""
        self.fields = dict(payload.get("fields") or {})
        self.fields_version += 1

    @workflow.signal
    def child_settled(self, payload: dict[str, Any]) -> None:
        """A child work item this one opened is closed (S38-12): the join re-reads its children."""
        self.children_settled += 1

    @workflow.signal
    def action_settled(self, payload: dict[str, Any]) -> None:
        """A transition action is settled: succeeded, failed, rejected (S20-05)."""
        self.settled_actions[str(payload.get("action_id"))] = str(payload.get("status"))

    @workflow.signal
    def control(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "stop":
            self.stopped = True
            self.paused = False
        elif action == "migrate":
            self.workflow_override = message.get("workflow", message)
            self.state_mapping = dict(message.get("state_mapping", {}))
            self.migration = dict(message)
        elif action == "rerun_stage":
            self.rerun_requested = True
            self._look_up_resume()

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "cost_usd": self.cost_usd,
            "attempts": dict(self.attempts),
            "current_run": self.current_run,
            "last_run": self.last_run,
            "paused": self.paused,
            "stopped": self.stopped,
            "pending_request": self.pending_request,
            "waiting_until": self.waiting,
            "action": self.current_action,
        }

    # ───────────────────────── main loop ─────────────────────────

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = InterpreterInput(**payload)
        try:
            return await self._run(params, payload)
        except (asyncio.CancelledError, workflow.ContinueAsNewError):
            raise
        except Exception as exc:
            # The workflow dies: say so BEFORE dying. Without this trace, the ticket stays in its
            # original state and nothing, neither screen nor event, tells "it is waiting" from "it
            # is dead". The bench of 2026-09-24 lost two tickets that way.
            with contextlib.suppress(Exception):
                await execute_on_its_queue(
                    record_workflow_failure,
                    {
                        "project_id": params.project_id,
                        "work_item_id": params.work_item_id,
                        "message": message_of(exc),
                        "activity": activity_of(exc),
                        "state": self.state,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1)),
                )
            raise

    async def _run(self, params: InterpreterInput, payload: dict[str, Any]) -> dict[str, Any]:
        self.attempts.update(params.attempts)
        self.attempt_resets.update(params.attempts_reset)
        if params.parked:
            self.resume_point = (params.parked[0], params.parked[1], params.parked[2])
        self.suspension = dict(params.suspended) if params.suspended else None
        self.project_id = params.project_id
        self.work_item_id = params.work_item_id
        self.cost_usd = params.cost_usd

        context = await execute_on_its_queue(
            load_context,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        engine = WorkflowEngine(Workflow.model_validate(context["workflow"]))
        self._start_from(params, engine, context)
        self._restore_attempts(context)
        self.ceilings = dict(context.get("attempts_ceilings") or {})

        await self._mirror(params, "started")

        while not engine.is_terminal(self.state) and not self.stopped:
            await workflow.wait_condition(lambda: not self.paused or self.stopped)
            if self.stopped:
                break
            if self.workflow_override is not None:
                if workflow.patched(MIGRATION_BY_DEFINITION):
                    engine = await self._migrate_by_definition(params, engine)
                else:
                    engine = self._migrate(engine)
                continue

            transition = engine.select_transition(self.state, self.last_outcome, self.last_outputs)
            self.last_outcome = None
            await self._note_transition_taken(params, transition)
            if transition is None:
                moved = await self._wait_external(engine, context)
                if moved == _MIGRATION:
                    continue
                if moved is None:
                    break
                self.state = moved
                await self._mirror(params, "external event")
                continue

            kind = engine.actor_kind(transition)
            # A job (ADR 0056) runs in a run like an agent; no history holds one before it existed.
            if kind in ("agent", "job"):
                decision = await self._run_stage(params, engine, transition, context)
            elif kind == "decision":
                # ADR 0121: no history holds one, the validator refused it until it ran (S52-01).
                decision = await self._run_decision(params, engine, transition, context)
            elif kind == "human":
                decision = await self._wait_human(params, engine, transition)
            elif kind == "train":
                decision = await self._run_train(params, engine, transition)
            else:
                decision = await self._run_system(params, engine, transition)

            if decision is None:
                continue
            self._note_the_resume_point(engine, transition, kind, decision)
            self.ledger_run = self._recorded_run(kind, transition, decision)
            self.state = decision.next_state
            await self._mirror(params, decision.reason)

            if workflow.info().get_current_history_length() > params.history_threshold:
                workflow.continue_as_new(
                    {
                        **payload,
                        "resume_from": self.state,
                        "attempts": dict(self.attempts),
                        "cost_usd": self.cost_usd,
                        "attempts_reset": dict(self.attempt_resets),
                        "parked": list(self.resume_point) if self.resume_point else None,
                        "suspended": self.suspension,
                        "last_outputs": self.last_outputs,
                    }
                )

        await self._release_suspension()  # the work item ends: no sandbox left waiting
        await execute_on_its_queue(
            tracker_activities.close_out,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        return {"state": self.state, "cost_usd": self.cost_usd, "stopped": self.stopped}

    def _start_from(self, params: InterpreterInput, engine: WorkflowEngine, context: dict[str, Any]) -> None:
        """The state, spend and fields the interpreter starts from."""
        if workflow.patched(TICKET_SPEND_FROM_LEDGER):
            # A restarted interpreter starts from what the ticket has cost, not from zero.
            self.cost_usd = max(self.cost_usd, float(context.get("ticket_cost_usd", 0.0)))
        self.state = params.resume_from or context.get("state") or engine.initial_state
        # Across a `continue_as_new` the payload carries them; a restarted interpreter reads the last
        # run's back from the database (S48-01).
        self.last_outputs = params.last_outputs or context.get("last_outputs") or None
        if self.fields_version == 0:  # a `fields_changed` that arrived before the read is newer
            self.fields = dict(context.get("fields") or {})

    def _restore_attempts(self, context: dict[str, Any]) -> None:
        """The attempts each transition spent before this interpreter, read from its runs (#550).

        Only a run that ended without success counts: one still queued or running, or a last run that
        succeeded, keeps its number, and the stage awaits it or reads its result back (ADR 0008).
        The attempts budget counts them too, as if nothing had died.
        """
        spent = {str(k): int(v) for k, v in (context.get("attempts_spent") or {}).items()}
        behind = {key: count for key, count in spent.items() if count > self.attempts[key]}
        # The condition first, `patched` next: a history without runs behind never asks.
        if behind and workflow.patched(ATTEMPTS_FROM_RUNS):
            for key, count in behind.items():  # `Counter.update` would add them
                self.attempts[key] = count
        # A new pass (C1): its runs are numbered past the earlier passes', and those count for nothing
        # in its budget. `max`: across a `continue_as_new`, the pass's own attempts stay counted.
        earlier = {str(k): int(v) for k, v in (context.get("attempts_before_pass") or {}).items()}
        if earlier and workflow.patched(FRESH_ATTEMPTS_EACH_RECURRENCE):
            for key, count in earlier.items():
                self.attempts[key] = max(self.attempts[key], count)
                self.attempt_resets[key] = max(self.attempt_resets[key], count)
