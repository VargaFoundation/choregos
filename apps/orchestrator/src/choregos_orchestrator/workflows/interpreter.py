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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from ..wire_names import both_names, read_key
from .activity_names import named
from .scheduling import execute_on_its_queue

#: The maximum wait of a run IN THE QUEUE (six hours), the same value as
#: `activities.stage.FILE_MAX_MINUTES`: written here because a workflow does not import the
#: activities module, and because it goes into Temporal bounds.
FILE_MAX_MINUTES = 360


with workflow.unsafe.imports_passed_through():
    from choregos_contracts import StageResult, StageStatus, Workflow
    from choregos_contracts.workflow import effet_de_la_transition
    from choregos_core import Decision, WorkflowEngine
    from choregos_core.dsl.dates import DateIllisible, echeance
    from choregos_core.dsl.trains import approbation_du_train, env_du_train
    from choregos_core.gates import ACTION_REGLEE, GateOutcome, unchanged_run

    from ..activities import actions as action_activities
    from ..activities import children as children_activities
    from ..activities import decision as decision_activities
    from ..activities import gates as gate_activities
    from ..activities import scm as scm_activities
    from ..activities import stage as stage_activities
    from ..activities import suspension as suspension_activities
    from ..activities import tracker as tracker_activities
    from ..activities.execution import START_REFUSED
    from ..activities.interpretation import (
        activite_de,
        escalation_context,
        load_context,
        message_de,
        record_migration,
        record_workflow_failure,
        signal_train,
    )

DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)
NO_RETRY = RetryPolicy(maximum_attempts=1)
#: For an activity that only POLLS (`await_run`): idempotent, hence replayable. It used to run
#: with no retry, and a worker restart during a run (a `helm upgrade`, bench of 2026-09-24) made it
#: expire on its heartbeat and killed the ticket.
POLL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=20,
)
HISTORY_THRESHOLD = 20_000
#: `migrate` receives the target definition in the signal, records it and no longer kills the ticket
#: (ADR 0031, S16-05). Histories from before this marker replay the old path.
MIGRATION_BY_DEFINITION = "migration-par-definition"  # lang: fr-ok — a patch id is never renamed
#: Returned by `_wait_external` when a migration wakes it: the state does not move, the loop migrates.
_MIGRATION = "\x00migration"
#: A system transition proposes a governed action, at a date (S20-05). Histories from before this
#: marker had none: their system transitions only know guarantees.
TRANSITION_ACTIONS = "actions-de-transition"  # lang: fr-ok — a patch id is never renamed
#: The net of a waiting action: re-read from the database at this pace, in case `action_settled` is lost.
ACTION_REREAD = timedelta(hours=6)
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
REPRISE_APRES_ESCALADE = RESUME_AFTER_ESCALATION  # lang: fr-ok alias:internal
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
#: The safety net of a join: the children re-read at this pace, in case a `child_settled` is lost.
CHILDREN_REREAD = timedelta(minutes=30)

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
#: The non-retryable error types `prepare_stage` raises for a configuration an operator can fix.
#: Any other failure — a bug, an unreachable database once its retries are spent, an admission rule,
#: a suspected prompt injection — still fails the interpreter, as before. Three of them were born
#: French: both spellings are read (S45-03), since a history recorded before S45-39 holds the French.
CONFIGURATION_REFUSALS = frozenset(
    {
        "ModelResolutionError",
        "SkillPendingReview",
        "KnowledgeRefused",
        "BackendDisabled",  # S42-07c: the platform administrator switched the backend off
        "DecisionRefused",  # ADR 0121: no `/v1/systemone` on the gateway, no `decide` in the catalogue
        *both_names("AgentUnavailable", "InvalidInstructions", "SkillMissing"),
    }
)


@dataclass(frozen=True)
class Refusal:
    """A typed refusal an activity raises, and how the work item escalates on it."""

    types: frozenset[str]  # the non-retryable error types that are this refusal
    marker: str  # the `workflow.patched` id behind which it escalates
    reason: str  # the state change's reason
    question: str  # the request's question, on the question state
    kind: str  # the request's `reason`


@dataclass
class InterpreterInput:
    """What is needed to start (or resume) the interpretation of a ticket."""

    project_id: str
    project_slug: str
    work_item_id: str
    tracker_key: str
    resume_from: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    history_threshold: int = HISTORY_THRESHOLD
    #: The attempts already counted when the ticket resumed, per transition (see `_counted_attempts`).
    attempts_reset: dict[str, int] = field(default_factory=dict)
    #: Where to resume a parked ticket: `[parked state, origin state, transition]`.
    parked: list[str] | None = None
    #: The run whose sandbox is suspended while a person answers (S35-03), across a
    #: `continue_as_new`: `{run_id, key, until, answered}`.
    suspended: dict[str, Any] | None = None
    #: The outputs of the last stage, which a transition's `when` reads (S48-01), across a
    #: `continue_as_new`.
    last_outputs: dict[str, Any] | None = None


@dataclass
class InterpreterStatus:
    state: str
    cost_usd: float
    attempts: dict[str, int]
    current_run: str | None
    paused: bool
    stopped: bool
    pending_request: str | None = None


@workflow.defn(name="WorkflowInterpreter", sandboxed=False)
class WorkflowInterpreter:
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
                        "message": message_de(exc),
                        "activity": activite_de(exc),
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

    # ───────────────────────── stages ─────────────────────────

    async def _run_stage(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        if engine.agent_of(transition) is None and engine.job_of(transition) is None:
            return engine.retry_or_escalate(
                transition, self._counted_attempts(transition.key), "agent actor not found"
            )
        key = transition.key
        resumed = await self._resume_suspension(params, key)
        if resumed is not None:
            run_id, budget = resumed
            self.current_run = run_id
            # It was suspended because its executor can: asked again, it is suspended again.
            capabilities = ["resume", "suspend"]
        else:
            attempt = self.attempts[key]
            self.attempts[key] = attempt + 1

            stage = {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": key,
                "from_state": transition.from_,
                "to_state": transition.to,
                "actor": transition.by or "",
                "attempt": attempt + 1,
                "outputs": list(transition.outputs),
                "inputs": list(transition.inputs),
                "output_types": dict(transition.output_types),
            }
            stage |= engine.run_request(transition)
            prepare = execute_on_its_queue(
                stage_activities.prepare_stage,
                stage,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )
            try:
                prepared = await prepare
            except ActivityError as error:
                escalated = await self._configuration_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                return escalated
            if prepared.get("refused") and workflow.patched(BUDGET_RESERVATION):
                refused = prepared["refused"]
                return await self._refused(
                    params, engine, transition, str(refused["reason"]), str(refused["detail"])
                )
            run_id = prepared["run_id"]
            self.current_run = run_id
            budget = prepared["stage_input"]["budget"]

            try:
                started = await execute_on_its_queue(
                    stage_activities.start_run,
                    {
                        "project_id": params.project_id,
                        "work_item_id": params.work_item_id,
                        "stage_input": prepared["stage_input"],
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=DEFAULT_RETRY,
                )
            except ActivityError as error:
                escalated = await self._start_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                self.current_run = None  # closed `failed` by `start_run`; nothing runs
                return escalated
            capabilities = list((started or {}).get("capabilities") or [])
        awaited = await execute_on_its_queue(
            stage_activities.await_run,
            {
                "project_id": params.project_id,
                "run_id": run_id,
                "timeout_minutes": int(budget["max_minutes"]) + 10,
            },
            start_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 20),
            # GLOBAL bound, all attempts included: without it, twenty resumptions of a
            # two-hour wait would make a ticket that never dies.
            schedule_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 90),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=POLL_RETRY,
        )
        result = StageResult.model_validate(awaited["result"])

        spend = await execute_on_its_queue(
            stage_activities.collect_spend,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        if "ticket_cost_usd" in spend and workflow.patched(TICKET_SPEND_FROM_LEDGER):
            self.cost_usd = float(spend["ticket_cost_usd"])  # the ledger: models AND tools
        else:
            self.cost_usd += float(spend.get("cost_usd", 0.0))
        await execute_on_its_queue(
            stage_activities.record_run_outcome,
            {"project_id": params.project_id, "run_id": run_id, "result": awaited["result"]},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        # The run's diff is what the front end SHOWS, not what the ticket depends on. A failure
        # here (SCM unreachable, project without a repository on a late image) must not kill a
        # stage that has just succeeded: it happened to RH-1 on 2026-09-24.
        try:
            await execute_on_its_queue(
                scm_activities.collect_run_artifacts,
                {"project_id": params.project_id, "work_item_id": params.work_item_id, "run_id": run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=NO_RETRY,
            )
        except ActivityError as exc:
            workflow.logger.warning("run diff not collected: %s", message_de(exc))
        self.current_run = None
        self.last_run = run_id  # `system` transitions evaluate their gates on this run
        # What the stage answered: the next selection reads it (S48-01). Taken from the result the
        # activity returned, so a replay reads the same values.
        self.last_outputs = result.outputs.model_dump(mode="json", exclude_none=True) or None

        outcomes = await self._gates(params, transition, run_id)

        ticket_budget = float(spend.get("ticket_budget_usd") or context.get("ticket_budget_usd", 0.0))
        if self.cost_usd > ticket_budget > 0:
            result = result.model_copy(
                update={
                    "status": StageStatus.NEEDS_HUMAN,
                    "reason": "budget",
                    "summary": f"{result.summary} (work item budget exceeded)",
                }
            )
        for request in result.scope_changes_requested:
            await self._request_scope_change(params, transition, request)

        if (
            result.reason == "budget"
            and result.status is not StageStatus.DONE
            and workflow.patched(BUDGET_NEVER_RETRIES)
        ):
            # A person decides; the ticket is parked, and a request tells them why.
            result = result.model_copy(update={"status": StageStatus.NEEDS_HUMAN})
            decision = engine.after_budget_exceeded(transition, result.summary[:200])
        else:
            decision = engine.after_stage(
                transition,
                result,
                self._counted_attempts(key),
                outcomes,
                require_outputs=workflow.patched(DECLARED_OUTPUTS_REQUIRED),
                ceiling=_attempts_ceiling(context, engine.agent_of(transition)),
            )
            decision = self._identical_failure(engine, transition, result, decision)
            decision = await self._already_fixed(params, engine, transition, result, run_id, decision)
        if decision.next_state == engine.question_state() and result.status is StageStatus.NEEDS_HUMAN:
            await self._create_request(
                params,
                transition,
                kind="question" if result.questions else "approval",
                payload={
                    "question": result.questions[0].text if result.questions else result.summary,
                    "options": list(result.questions[0].options) if result.questions else [],
                    "summary": result.summary,
                    "run_id": run_id,
                },
            )
            if (
                result.questions
                and {"suspend", "resume"} <= set(capabilities)
                and workflow.patched(SUSPEND_AROUND_HUMAN)
            ):
                await self._suspend(params, run_id, key)
        return decision

    async def _already_fixed(
        self,
        params: InterpreterInput,
        engine: WorkflowEngine,
        transition: Any,
        result: Any,
        run_id: str,
        decision: Any,
    ) -> Any:
        """A step that succeeded without changing anything (S22-47, #539): when its state declares an
        "already fixed" exit, that exit's guarantees are read on this run. Proven, the work item ends
        there — no test, review or pull request on an empty change; unproven, the step escalates
        with what the evidence lacks, and a person decides. Any other step keeps its decision."""
        if decision.next_state != transition.to or decision.escalated or decision.retried:
            return decision
        exit_ = engine.already_fixed_exit(transition.from_)
        # The condition first, `patched` next: a history without such an exit never asks.
        if exit_ is None or not unchanged_run(result) or not workflow.patched(ALREADY_FIXED_ON_EVIDENCE):
            return decision
        outcomes = await self._gates(params, exit_, run_id)
        decided = engine.after_unchanged_stage(transition, exit_, outcomes)
        if decided.escalated and decided.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": f"`{transition.key}` changed nothing: {decided.reason}. Close the work "
                    "item if it is already fixed, or replay the implementation.",
                    "options": [],
                    "summary": result.summary,
                    "run_id": run_id,
                    "reason": "no_change_unproven",
                },
            )
        return decided

    # ───────────────────────── a decision step (ADR 0121) ─────────────────────────

    async def _run_decision(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        """A decision model answers the step's questions through the project's gateway: one activity,
        no run to start or await. Its answers are the step's outputs, which `when` reads on the next
        transitions as it reads an agent's (ADR 0106); its gates and its `on_fail` apply as after a run.

        A failed decision keeps the outputs the step read: its next attempt is asked about the same
        state. A refusal of the configuration escalates the work item, as `prepare_stage`'s does."""
        actor = engine.decision_of(transition)
        assert actor is not None, "a decision step without its decision actor"
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1
        try:
            decided = await execute_on_its_queue(
                decision_activities.decide,
                decision_activities.decision_request(
                    project_id=params.project_id,
                    work_item_id=params.work_item_id,
                    transition=transition,
                    decision=actor,
                    attempt=attempt + 1,
                    last_outputs=self.last_outputs,
                ),
                start_to_close_timeout=timedelta(
                    seconds=actor.timeout_seconds + decision_activities.DECISION_MARGIN_S
                ),
                retry_policy=DEFAULT_RETRY,
            )
        except ActivityError as error:
            escalated = await self._configuration_refused(params, engine, transition, error)
            if escalated is None:
                raise
            return escalated
        run_id = str(decided["run_id"])
        result = StageResult.model_validate(decided["result"])
        self.cost_usd = float(decided["ticket_cost_usd"])  # the ledger, as after a run
        self.last_run = run_id
        if result.status is StageStatus.DONE:
            self.last_outputs = result.outputs.model_dump(mode="json", exclude_none=True) or None
        outcomes = await self._gates(params, transition, run_id)
        decision = engine.after_stage(
            transition,
            result,
            self._counted_attempts(key),
            outcomes,
            ceiling=_attempts_ceiling(context, actor),
        )
        decision = self._identical_failure(engine, transition, result, decision)
        if decision.escalated and decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": f"The decision `{key}` did not decide: {result.summary}",
                    "options": [],
                    "summary": result.summary,
                    "run_id": run_id,
                    "reason": result.reason or "decision",
                },
            )
        return decision

    # ──────────────────── a sandbox kept across an answer (S35-03) ────────────────────

    async def _suspend(self, params: InterpreterInput, run_id: str, key: str) -> None:
        """The agent asked a question: its sandbox waits, suspended, for the answer."""
        done = await execute_on_its_queue(
            suspension_activities.suspend_run,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        if done.get("suspended"):
            self.suspension = {"run_id": run_id, "key": key, "until": done["until"], "answered": False}

    def _suspension_left(self) -> timedelta | None:
        """How long the suspended sandbox may still wait; None when nothing is suspended."""
        if self.suspension is None:
            return None
        until = datetime.fromisoformat(str(self.suspension["until"]))
        return max(until - workflow.now(), timedelta(0))

    async def _release_suspension(self) -> None:
        """The sandbox will not be resumed: destroyed now rather than at its bound."""
        suspended, self.suspension = self.suspension, None
        if suspended is None:
            return
        await execute_on_its_queue(
            suspension_activities.release_suspended_run,
            {"project_id": self.project_id, "run_id": suspended["run_id"]},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )

    async def _resume_suspension(self, params: InterpreterInput, key: str) -> tuple[str, Any] | None:
        """The answered question's run goes on, if its sandbox still waits: `(run_id, budget)`.
        None — and the stage replays as before — when nothing was suspended for this transition,
        the ticket came back another way (a replay asked for), or the sandbox is gone."""
        suspended = self.suspension
        if suspended is None:
            return None
        if suspended["key"] != key or not suspended.get("answered"):
            await self._release_suspension()
            return None
        self.suspension = None
        self.resumptions += 1
        resumed = await execute_on_its_queue(
            suspension_activities.resume_run,
            {
                "project_id": params.project_id,
                "run_id": suspended["run_id"],
                "answer_id": f"{suspended['run_id']}/{self.resumptions}",
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        if not resumed.get("resumed"):
            return None
        return str(suspended["run_id"]), resumed["budget"]

    def _identical_failure(self, engine: WorkflowEngine, transition: Any, result: Any, decision: Any) -> Any:
        """A retry of a stage that failed exactly like its previous attempt escalates (S27-07)."""
        from choregos_core.dsl.engine import failure_signature

        signature = failure_signature(result)
        previous = self.failure_signatures.pop(transition.key, None)
        # A success, or an escalation (a person takes over; a replay of the stage starts afresh):
        # nothing to compare the next attempt with.
        if signature is None or decision.escalated:
            return decision
        if decision.retried and signature == previous and workflow.patched(IDENTICAL_FAILURE_ESCALATES):
            return engine.escalate(
                transition, f"the same failure twice in a row, not retried again: {signature[:200]}"
            )
        self.failure_signatures[transition.key] = signature
        return decision

    async def _refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, reason: str, detail: str
    ) -> Any:
        """The stage did not start (ADR 0047) — too little budget left, or the work item ran every
        stage the policy allows: no key, no run, a person decides."""
        if reason == "max_stages":
            decision = engine.after_stage_cap(transition, detail[:200])
        else:
            decision = engine.after_budget_exceeded(transition, detail[:200])
        if decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={"question": detail, "options": [], "summary": detail, "reason": reason},
            )
        return decision

    async def _configuration_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """`prepare_stage` refused the stage on its configuration (#543): the work item escalates,
        as after a failed attempt, with the refusal as its reason, and waits for an operator to fix
        the configuration and replay the stage. `None` — the interpreter fails, as before — for any
        other error, for a history recorded before the marker, and for a transition with nowhere
        to escalate to: going back to the state it started from would refuse again, forever."""
        detail = message_de(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=CONFIGURATION_REFUSALS,
                marker=STAGE_CONFIG_REFUSAL_ESCALATES,
                reason=f"configuration refused: {detail}",
                question=f"The stage `{transition.key}` cannot start: {detail}. "
                "Fix the configuration, then replay the stage.",
                kind="configuration",
            ),
        )

    async def _start_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """The executor refused the run on the deployment's configuration (#750, S38-13e): the run is
        closed `failed` with the reason, and the work item escalates at once with it — another attempt
        would be refused the same way. Any other error of `start_run` is `None`, and fails the
        interpreter as before."""
        detail = message_de(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=frozenset({START_REFUSED}),
                marker=START_REFUSAL_ESCALATES,
                reason=f"the executor refused the run: {detail}",
                question=f"The stage `{transition.key}` could not start: {detail}. "
                "Fix the deployment or the policy, then replay the stage.",
                kind="configuration",
            ),
        )

    async def _no_branch_refused(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, error: ActivityError
    ) -> Any:
        """`open_pull_request` found no branch to open a pull request from (S22-46, #539): the
        implementation changed nothing. The work item escalates with that reason, instead of the
        interpreter failing on GitHub's 422. Any other error — a 403, a 5xx once its retries are
        spent — is `None`, and fails the interpreter as before."""
        detail = message_de(error)
        return await self._refusal_escalates(
            params,
            engine,
            transition,
            error,
            Refusal(
                types=frozenset({scm_activities.NO_BRANCH}),
                marker=OPEN_PR_WITHOUT_BRANCH_ESCALATES,
                reason=detail,
                question=f"`{transition.key}` cannot open a pull request: {detail}. "
                "Check the work item (already fixed?), then close it or replay the implementation.",
                kind="no_branch",
            ),
        )

    async def _refusal_escalates(
        self,
        params: InterpreterInput,
        engine: WorkflowEngine,
        transition: Any,
        error: ActivityError,
        refusal: Refusal,
    ) -> Any:
        """A typed, non-retryable refusal escalates the work item, as after a failed attempt, with
        its reason; a request asks its question when it lands on the question state. `None` for any
        other error, for a history recorded before its marker, and for a transition with nowhere to
        escalate to: going back to the state it started from would refuse again, forever."""
        cause = error.cause
        if not (isinstance(cause, ApplicationError) and cause.non_retryable and cause.type in refusal.types):
            return None
        decision = engine.escalate(transition, refusal.reason[:300])
        # The condition first, `patched` next: an older history replayed here answers no without
        # the marker being asked for an error the old code did not escalate.
        if decision.next_state == transition.from_ or not workflow.patched(refusal.marker):
            return None
        detail = message_de(error)
        if decision.next_state == engine.question_state():
            await self._create_request(
                params,
                transition,
                kind="approval",
                payload={
                    "question": refusal.question,
                    "options": [],
                    "summary": detail,
                    "reason": refusal.kind,
                },
            )
        return decision

    async def _gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]:
        if not transition.gates:
            return []
        outcomes = await self._evaluate_all_gates(params, transition, run_id)
        pending = [o for o in outcomes if o.pending]
        if pending and transition.timeout_hours:
            resolved = await self._await_async_gates(params, transition, run_id, [o.name for o in pending])
            # Replaced by name: the wait returns the pending guarantees only, or all of them once
            # they are read again (`GATES_REREAD_AFTER_WAIT`).
            fresh = {o.name for o in resolved}
            outcomes = [o for o in outcomes if o.name not in fresh] + resolved
        return outcomes

    async def _evaluate_all_gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]:
        """Every guarantee of the transition, read now — on entry, and again once the pending ones
        have their verdict: a synchronous one may have changed meanwhile (#359)."""
        from choregos_core import GateOutcome

        raw = await execute_on_its_queue(
            gate_activities.evaluate_gates,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "run_id": run_id,
                # What the transition declares it produces: the generic `outputs_present`
                # guarantee uses it when it has no explicit list.
                "expected_outputs": list(transition.outputs),
                "gates": [{"name": g.name, "params": g.params} for g in transition.gates],
            },
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=DEFAULT_RETRY,
        )
        return [
            GateOutcome(
                name=item["name"],
                passed=item["passed"],
                pending=item["pending"],
                detail=item["detail"],
                annotations=list(item.get("annotations", [])),
            )
            for item in raw
        ]

    async def _await_async_gates(
        self, params: InterpreterInput, transition: Any, run_id: str, names: list[str]
    ) -> list[Any]:
        """Wait for the external events (CI, review) then re-evaluate, until the timeout."""
        from choregos_core import GateOutcome

        deadline = timedelta(hours=transition.timeout_hours or 24)
        waited = timedelta()
        step = timedelta(minutes=5)
        while waited < deadline:
            received = await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                self._absorb(event)
            raw = await execute_on_its_queue(
                gate_activities.evaluate_gates,
                {
                    "project_id": params.project_id,
                    "work_item_id": params.work_item_id,
                    "run_id": run_id,
                    "expected_outputs": list(transition.outputs),
                    "gates": [
                        {"name": g.name, "params": g.params} for g in transition.gates if g.name in names
                    ],
                },
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
            outcomes = [
                GateOutcome(
                    name=item["name"], passed=item["passed"], pending=item["pending"], detail=item["detail"]
                )
                for item in raw
            ]
            if not any(o.pending for o in outcomes) or self.stopped:
                # The condition first, `patched` next: an old history replayed here answers no
                # without the marker being asked any earlier (S22-32).
                if not self.stopped and workflow.patched(GATES_REREAD_AFTER_WAIT):
                    complete = await self._evaluate_all_gates(params, transition, run_id)
                    again = [o.name for o in complete if o.pending]
                    if not again:
                        return complete
                    # A guarantee pending again (CI re-run on a new push): wait for it as before.
                    names = again
                else:
                    return outcomes
            waited += step
            if received is False:  # wait_condition expired with no signal
                continue
        return [GateOutcome(name=name, passed=False, detail="timed out") for name in names]

    def _absorb(self, event: dict[str, Any]) -> None:
        """Translate an incoming event into `last_outcome` for the next selection."""
        kind = event.get("type", "")
        if (
            kind in {"ci.run.failed", "scm.check.completed"}
            and event.get("payload", {}).get("conclusion") == "failure"
        ):
            self.last_outcome = "ci_failed"
        elif (
            kind == "scm.pr.review_submitted" and event.get("payload", {}).get("state") == "changes_requested"
        ):
            self.last_outcome = "changes_requested"
        elif kind == "scm.pr.merged":
            self.last_outcome = "merged"

    # ───────────────────────── human ─────────────────────────

    async def _wait_human(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        actor = engine.human_of(transition)
        sla_hours = (actor.sla_hours if actor else None) or 24
        is_task = transition.task is not None and workflow.patched(HUMAN_TASKS)
        if is_task:
            kind, payload = (
                "task",
                {
                    "summary": transition.task.title or f"Task before `{transition.to}`",
                    "instructions": transition.task.instructions,
                    "form": transition.task.form,
                    "attest": transition.task.attest,
                },
            )
        else:
            kind, payload = "approval", {"summary": f"Approval needed to move to `{transition.to}`"}
        request = await self._create_request(
            params,
            transition,
            kind=kind,
            payload=payload,
            sla_hours=sla_hours,
        )
        self.pending_request = request["request_id"]
        timeout = timedelta(hours=transition.timeout_hours or sla_hours * 3)
        reminder_sent = False
        waited = timedelta()
        step = timedelta(hours=1)
        while waited < timeout:
            got = await wait_signal(
                lambda: bool(self.decisions) or self.stopped or self._rerun_playable(),
                min(step, timeout - waited),
            )
            if self.stopped:
                return None
            if self._suspension_left() == timedelta(0):
                # `sandbox.suspend_max_hours` passed: the sandbox goes (the executor's platform
                # deletes it at that instant anyway), and the answer will replay the stage.
                await self._release_suspension()
            if await self._replay_requested():
                return await self._resume("replay requested", self.pending_request, engine=engine)
            if self.decisions:
                decision = self.decisions.popleft()
                if self._answer_resumes(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    return await self._resume(
                        "answered", decision.get("request_id"), decision, self.pending_request, engine=engine
                    )
                await execute_on_its_queue(
                    tracker_activities.close_human_request,
                    {
                        "request_id": decision.get("request_id") or self.pending_request,
                        "decided_by": decision.get("decided_by", "a person"),
                        "decision": decision,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                self.pending_request = None
                approved = bool(decision.get("approved"))
                if is_task and approved and decision.get("values"):
                    # The task's values are fields: the API has already written them, an action
                    # date that depends on them is recomputed here.
                    self.fields = {**self.fields, **dict(decision["values"])}
                    self.fields_version += 1
                return engine.after_human(transition, approved, decision.get("reason") or "")
            waited += step
            if not got and not reminder_sent and waited >= timedelta(hours=sla_hours):
                reminder_sent = True
                await self._remind(params, actor, transition)
        escalate = engine.timeout_state() or transition.from_
        return (
            engine.after_human(transition, approved=False, reason="no human decision in time")
            if transition.on_reject
            else _decision(escalate, "no human decision in time")
        )

    async def _remind(self, params: InterpreterInput, actor: Any, transition: Any) -> None:
        from choregos_core import Message

        sla = actor.sla_hours if actor else 24

        await execute_on_its_queue(
            tracker_activities.notify,
            {
                "project_id": params.project_id,
                "message": Message(
                    title=f"Reminder: {params.tracker_key} is waiting for a decision",
                    body=f"Transition `{transition.key}` has been waiting for {sla} h.",
                    severity="warning",
                ).model_dump(mode="json"),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=NO_RETRY,
        )

    async def _create_request(
        self,
        params: InterpreterInput,
        transition: Any,
        *,
        kind: str,
        payload: dict[str, Any],
        sla_hours: int = 24,
    ) -> dict[str, Any]:
        # The scheduling wrapper returns an `Any` where `execute_activity` had a typed overload:
        # going through an annotated name keeps the signature's promise.
        created: dict[str, Any] = await execute_on_its_queue(
            tracker_activities.create_human_request,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": transition.key,
                "kind": kind,
                "payload": payload,
                "sla_hours": sla_hours,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        return created

    async def _request_scope_change(self, params: InterpreterInput, transition: Any, request: Any) -> None:
        await self._create_request(
            params,
            transition,
            kind="scope_change",
            payload={"paths": list(request.paths), "justification": request.justification},
        )

    # ───────────────────────── system and train ─────────────────────────

    async def _run_system(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1

        if transition.spawn is not None and workflow.patched(FAN_OUT):
            return await self._run_spawn(params, engine, transition)
        if transition.action is not None and workflow.patched(TRANSITION_ACTIONS):
            return await self._run_action(params, engine, transition, attempt + 1)

        # What the platform does is WRITTEN (`does`, #175); a workflow published before carried it in
        # the target state's name, and is still read that way: a recorded history replays identically.
        effect = effet_de_la_transition(transition)
        if effect == "open_pr":
            try:
                await execute_on_its_queue(
                    scm_activities.open_pull_request,
                    {"project_id": params.project_id, "work_item_id": params.work_item_id},
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=DEFAULT_RETRY,
                )
            except ActivityError as error:
                escalated = await self._no_branch_refused(params, engine, transition, error)
                if escalated is None:
                    raise
                return escalated
        outcomes = await self._gates(params, transition, self.current_run or self.last_run or "")
        if effect == "merge_pr" and all(not o.blocking and not o.pending for o in outcomes):
            await execute_on_its_queue(
                scm_activities.enqueue_merge,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
        return engine.after_gates(transition, outcomes, self._counted_attempts(key))

    async def _run_action(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, attempt: int
    ) -> Any:
        """A transition with an action (ADR 0035, S20-05): wait for its date, propose it, wait until
        it is SETTLED (decided by a person or by the policy, then played by its `ActionWorkflow`,
        never here), and judge `action_succeeded` with the other guarantees."""
        spec = transition.action
        if spec.not_before and not await self._wait_for_the_date(spec.not_before):
            return None  # stopped, or a migration to apply: the loop sees to it
        proposed = await execute_on_its_queue(
            named(
                action_activities.propose_transition_action, action_activities.proposer_l_action_de_transition
            ),
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition": transition.key,
                "attempt": attempt,
                "workflow_id": workflow.info().workflow_id,
                "action": spec.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        refusal = read_key(proposed, "refusal")
        if refusal:
            refusal_outcome = GateOutcome(
                "action_succeeded", False, detail=f"the action cannot be proposed: {refusal}"
            )
            return engine.after_gates(transition, [refusal_outcome], self._counted_attempts(transition.key))
        action_id, action_status = str(proposed["action_id"]), str(proposed["status"])
        self.current_action = action_id
        while action_status not in ACTION_REGLEE and not self.stopped:
            await wait_signal(lambda: action_id in self.settled_actions or self.stopped, ACTION_REREAD)
            if action_id in self.settled_actions:
                action_status = self.settled_actions.pop(action_id)
            elif not self.stopped:
                reread = await execute_on_its_queue(
                    named(action_activities.action_state, action_activities.etat_de_l_action),
                    action_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                action_status = str(reread["status"])
        self.current_action = None
        if self.stopped:
            return None
        other_gates = transition.model_copy(
            update={"gates": [g for g in transition.gates if g.name != "action_succeeded"]}
        )
        outcomes = await self._gates(params, other_gates, self.last_run or "")
        return engine.after_action(
            transition, action_status, outcomes, self._counted_attempts(transition.key)
        )

    async def _run_spawn(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        """A fan-out and its join (S38-12, ADR 0057): open the children, then wait until every one is
        settled — woken by `child_settled`, re-reading them every `CHILDREN_REREAD` in case a signal
        is lost —, and judge the join with the transition's other gates. Opening is idempotent (the
        children's keys are the parent's with a rank): a migration or a retry finds them again."""
        spec = transition.spawn.model_dump(mode="json")
        opened = await execute_on_its_queue(
            children_activities.spawn_children,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "workflow_id": workflow.info().workflow_id,
                "spawn": spec,
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        if opened.get("refused"):
            refused = GateOutcome(
                "children_settled", False, detail=f"the children cannot be opened: {opened['refused']}"
            )
            return engine.after_gates(transition, [refused], self._counted_attempts(transition.key))
        children = list(opened["children"])
        while not self.stopped and self.workflow_override is None:
            seen = self.children_settled
            state = await execute_on_its_queue(
                children_activities.children_state,
                {"work_item_id": params.work_item_id, "children": children, "spawn": spec},
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
            if not state["pending"]:
                break
            self.waiting = f"{len(state['pending'])} of {len(children)} child work items not settled"
            await wait_signal(
                lambda seen=seen: (
                    self.children_settled != seen or self.stopped or self.workflow_override is not None
                ),
                CHILDREN_REREAD,
            )
        self.waiting = None
        if self.stopped or self.workflow_override is not None:
            return None  # stopped, or a migration to apply: the loop sees to it
        joined = GateOutcome("children_settled", bool(state["passed"]), detail=str(state["detail"]))
        outcomes = await self._gates(params, transition, self.last_run or "")
        return engine.after_gates(transition, [joined, *outcomes], self._counted_attempts(transition.key))

    async def _wait_for_the_date(self, expression: str) -> bool:
        """Wait for `not_before`, RECOMPUTED at every change of the fields: moving the date re-arms
        the timer, bringing it before the present time makes it leave. A missing or unreadable
        field: we wait for it to be filled in, we do not leave without a date. False when the
        ticket stops or a migration interrupts it."""
        while not self.stopped and self.workflow_override is None:
            seen_version = self.fields_version
            try:
                target = echeance(expression, self.fields)
            except DateIllisible:
                target = None
            now = workflow.now()
            if target is not None and target <= now:
                if self.paused:  # the date passed during a pause: we wait for the resumption
                    await workflow.wait_condition(
                        lambda: not self.paused or self.stopped or self.workflow_override is not None
                    )
                    continue
                self.waiting = None
                return True
            self.waiting = target.isoformat() if target is not None else f"{expression} (unknown)"
            await wait_signal(
                lambda seen_version=seen_version: (
                    self.fields_version != seen_version or self.stopped or self.workflow_override is not None
                ),
                (target - now) if target is not None else None,
            )
        self.waiting = None
        return False

    async def _run_train(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        """Board the ticket, then wait for ITS train to have delivered: `finish_release` and `rollback`
        warn it (ADR 0041). The approval the workflow requires travels with it up to the departure."""
        env = env_du_train(transition)
        per_environment = workflow.patched(TRAIN_PER_ENVIRONMENT)
        boarding: dict[str, Any] = {
            "project_id": params.project_id,
            "project_slug": params.project_slug,
            "work_item_id": params.work_item_id,
            "env": env,
        }
        if per_environment:
            boarding["approval"] = dict(approbation_du_train(engine.wf, transition))
        await execute_on_its_queue(
            signal_train,
            boarding,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        timeout = timedelta(hours=transition.timeout_hours or 72)
        waited = timedelta()
        step = timedelta(minutes=10)
        while waited < timeout and not self.stopped:
            await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                if per_environment and not _for_this_environment(event, env):
                    # A finished staging is not prod: a ticket that takes two trains in a row
                    # used to take the second as delivered as soon as the first was.
                    self._absorb(event)
                    continue
                if event.get("type") in {"cd.rollout.completed", "cd.app.synced"}:
                    return engine.after_train(transition, ok=True)
                if event.get("type") in {"cd.rollout.aborted", "cd.app.degraded"}:
                    return engine.after_train(transition, ok=False)
                self._absorb(event)
            waited += step
        return engine.after_train(transition, ok=False)

    # ───────────────────────── resumption of a parked ticket ─────────────────────────

    def _note_the_resume_point(
        self, engine: WorkflowEngine, transition: Any, kind: str, decision: Any
    ) -> None:
        """A step that parks the ticket (question, escalation) says where it will resume."""
        if (
            kind != "human"
            and decision.next_state != transition.from_
            and (decision.escalated or decision.next_state == engine.question_state())
        ):
            self.resume_point = (decision.next_state, transition.from_, transition.key)

    def _counted_attempts(self, key: str) -> int:
        """The attempts that count for `max_attempts`: those after the last resumption."""
        return self.attempts[key] - self.attempt_resets[key]

    def _is_parked(self) -> bool:
        return self.resume_point is not None and self.resume_point[0] == self.state

    def _rerun_playable(self) -> bool:
        """ "Replay the stage" only plays on a parked ticket; elsewhere, it is forgotten."""
        if self.rerun_requested and not self._is_parked() and not self.resume_lookup_pending:
            self.rerun_requested = False
        return self.rerun_requested

    def _look_up_resume(self) -> None:
        """A request to resume a work item this interpreter did not park — it was started again
        from the database (#497): where it resumes is to be read before the request plays."""
        if self._is_parked():
            return
        # The condition first, `patched` next — and one id per request (see the marker).
        self.resume_lookups += 1
        if workflow.patched(f"{ESCALATION_CONTEXT_AFTER_RESTART}-{self.resume_lookups}"):
            self.resume_lookup_pending = True

    async def _replay_requested(self) -> bool:
        """`_rerun_accepted`, once a restarted interpreter has read where its work item resumes —
        which an answer waiting in `decisions` needs as well."""
        if self.resume_lookup_pending:
            await self._read_the_parking()
        return self._rerun_accepted()

    async def _read_the_parking(self) -> None:
        """Rebuilds `resume_point` from what the escalation left in the database (S22-41), and the
        transition's attempts: the replayed stage gets a new run number, not the failed run's."""
        self.resume_lookup_pending = False
        if self._is_parked():
            return
        found = await execute_on_its_queue(
            escalation_context,
            {"project_id": self.project_id, "work_item_id": self.work_item_id, "state": self.state},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        parked = found.get("parked")
        if not parked or parked[0] != self.state:
            return
        self.resume_point = (str(parked[0]), str(parked[1]), str(parked[2]))
        key = self.resume_point[2]
        self.attempts[key] = max(self.attempts[key], int(found.get("attempts") or 0))

    def _rerun_accepted(self) -> bool:
        """Does the requested rerun play? If not it is FORGOTTEN: left in place, it kept the wait
        condition true, and `_wait_human` consumed its hour-long delay at each turn, so on the dev
        tenant, on 9 October, an abandon request was recreated every 4 s.

        That is the case of a history where `patched` already answered no: the SDK MEMOISES the
        answer for the whole execution, and a ticket that had received an answer before the marker
        never takes the new path. It must then be reset before that answer."""
        if self._rerun_playable() and workflow.patched(RESUME_AFTER_ESCALATION):
            return True
        self.rerun_requested = False
        return False

    def _answer_resumes(self, decision: dict[str, Any]) -> bool:
        """An ANSWER to a question, on a parked ticket: `on_answer: resume`."""
        return (
            decision.get("kind") == "question"
            and self._is_parked()
            and workflow.patched(RESUME_AFTER_ESCALATION)
        )

    def _rerun_allowance(self, engine: WorkflowEngine, key: str) -> None:
        """A rerun leaves the stage ONE attempt before its cap (S27-13): the replayed stage plays
        once, and escalates again if it fails. Attempts spent before count, whatever the number of
        reruns, so `max_attempts` bounds the stage plus one attempt per explicit rerun."""
        transition = next((t for t in engine.wf.transitions if t.key == key), None)
        if transition is None:
            self.attempt_resets[key] = self.attempts[key]
            return
        allowed = engine.max_attempts(transition)
        ceiling = _attempts_ceiling({"attempts_ceilings": self.ceilings}, engine.agent_of(transition))
        if ceiling is not None:
            allowed = min(allowed, ceiling)
        self.attempt_resets[key] = max(self.attempt_resets[key], self.attempts[key] - (allowed - 1))

    async def _resume(self, why: str, *requests: Any, engine: WorkflowEngine | None = None) -> Decision:
        """Brings the parked work item back to the state it left: pending requests are closed (the
        abandon offered is moot), and the stage gets one more attempt, not a new allowance."""
        assert self.resume_point is not None
        _, origin, key = self.resume_point
        answer = next((d for d in requests if isinstance(d, dict)), None)
        for request_id in dict.fromkeys(d for d in requests if isinstance(d, str) and d):
            await execute_on_its_queue(
                tracker_activities.close_human_request,
                {
                    "request_id": request_id,
                    "decided_by": (answer or {}).get("decided_by", "the platform"),
                    "decision": answer
                    if answer and answer.get("request_id") == request_id
                    else {"approved": False, "superseded": f"the work item resumed ({why})"},
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
        self.pending_request = None
        self.rerun_requested = False
        self.resume_point = None
        # The condition first, `patched` next: a rerun asks its marker, an answer its own.
        marker = ANSWER_GRANTS_ONE_ATTEMPT if answer is not None else RERUN_GRANTS_ONE_ATTEMPT
        if engine is not None and workflow.patched(marker):
            self._rerun_allowance(engine, key)
        else:
            self.attempt_resets[key] = self.attempts[key]
        return Decision(next_state=origin, reason=f"{why}: resumes at `{origin}`")

    # ───────────────────────── utilities ─────────────────────────

    async def _migrate_by_definition(
        self, params: InterpreterInput, engine: WorkflowEngine
    ) -> WorkflowEngine:
        """`migrate`: the target definition arrives in the signal, read and checked by the API.

        The ticket's pin follows it (a `continue_as_new` will reload it). A migration that became
        impossible (the state moved since the API's check) leaves the ticket alive, on its
        definition, and records it: `_migrate` used to kill it by raising inside the workflow.
        """
        requested, self.migration, self.workflow_override = self.migration, {}, None
        self.migrations += 1
        info = workflow.info()
        record = {
            "project_id": params.project_id,
            "work_item_id": params.work_item_id,
            # Recorded payload key (ADR 0098 §4): never renamed.
            "cle": f"{info.workflow_id}/{info.run_id}/{self.migrations}",
            "from_state": self.state,
        }
        try:
            target = Workflow.model_validate(requested.get("workflow") or {})
            new_state = engine.can_migrate_to(target, self.state, dict(requested.get("state_mapping") or {}))
        except ValueError as refusal:  # a pydantic ValidationError is one
            await execute_on_its_queue(
                record_migration,
                # Recorded payload key (ADR 0098 §4): never renamed.
                {**record, "refus": str(refusal)[:2000]},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            return engine
        await execute_on_its_queue(
            record_migration,
            {
                **record,
                "to_state": new_state,
                "workflow_def_id": requested.get("workflow_def_id"),
                "workflow": f"{target.metadata.name}@{target.metadata.version}",
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self.state = new_state
        await self._mirror(params, f"migrated to {target.metadata.name}@{target.metadata.version}")
        return WorkflowEngine(target)

    def _migrate(self, engine: WorkflowEngine) -> WorkflowEngine:
        """Old path, kept to replay the histories from before `MIGRATION_BY_DEFINITION`."""
        override = self.workflow_override or {}
        self.workflow_override, self.migration = None, {}
        definition = override.get("workflow") or override.get("json") or override
        target = Workflow.model_validate(definition)
        new_engine = WorkflowEngine(target)
        self.state = WorkflowEngine(engine.wf).can_migrate_to(target, self.state, self.state_mapping)
        return new_engine

    async def _wait_external(self, engine: WorkflowEngine, context: dict[str, Any]) -> str | None:
        """A waiting state with no outgoing transition: the human board decides.

        A requested migration wakes it too (`_MIGRATION`): a parked ticket has no other decision
        point, and would otherwise wait for an event that may never come. A history from before
        `MIGRATION_BY_DEFINITION` was not woken: it replays waiting for the next event, as then.
        """
        while not self.stopped:
            left = self._suspension_left()
            try:
                await workflow.wait_condition(
                    lambda: (
                        bool(self.inbox)
                        or bool(self.decisions)
                        or self.stopped
                        or bool(self.migration)
                        or self._rerun_playable()
                    ),
                    timeout=left,
                )
            except TimeoutError:
                # `sandbox.suspend_max_hours` passed without an answer: the sandbox goes, and
                # the answer, when it comes, replays the stage.
                await self._release_suspension()
                continue
            if await self._replay_requested():
                return (
                    await self._resume("replay requested", self.pending_request, engine=engine)
                ).next_state
            if self.migration and not (self.inbox or self.decisions or self.stopped):
                if workflow.patched(MIGRATION_BY_DEFINITION):
                    return _MIGRATION
                await workflow.wait_condition(
                    lambda: bool(self.inbox) or bool(self.decisions) or self.stopped
                )
            while self.inbox:
                event = self.inbox.popleft()
                if event.get("type") == "tracker.item.moved":
                    to_status = event.get("payload", {}).get("to_status")
                    for name, state in engine.wf.states.items():
                        if state.tracker and state.tracker.status == to_status:
                            return name
                self._absorb(event)
            while self.decisions:
                decision = self.decisions.popleft()
                if self._answer_resumes(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    resumed = await self._resume(
                        "answered", decision.get("request_id"), decision, engine=engine
                    )
                    return resumed.next_state
                if decision.get("approved"):
                    transitions = engine.wf.transitions_from(self.state)
                    if transitions:
                        return transitions[0].to
        return None

    async def _mirror(self, params: InterpreterInput, reason: str) -> None:
        mirrored: dict[str, Any] = {
            "project_id": params.project_id,
            "work_item_id": params.work_item_id,
            "state": self.state,
            "reason": reason,
        }
        if (
            self.resume_point is not None
            and self._is_parked()
            and workflow.patched(ESCALATION_CONTEXT_STORED)
        ):
            mirrored["parked"] = list(self.resume_point)  # where it resumes, for a restarted interpreter
        await execute_on_its_queue(
            tracker_activities.mirror_state,
            mirrored,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        await execute_on_its_queue(
            tracker_activities.update_status_comment,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )


async def wait_signal(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """`workflow.wait_condition` that returns `False` on expiry instead of raising.

    (In the Temporal SDK, a `wait_condition` that expires raises `asyncio.TimeoutError`; here we
    want to tell "the condition arrived" from "the delay elapsed".)
    """
    if timeout is None:
        await workflow.wait_condition(condition)
        return True
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True


def _decision(state: str, reason: str) -> Any:
    from choregos_core import Decision

    return Decision(next_state=state, escalated=True, reason=reason)


def _for_this_environment(event: dict[str, Any], env: str) -> bool:
    """A deployment event counts for the waiting train if it aims at its environment.
    With no environment named (an event carried by hand, from before ADR 0041), it counts."""
    aimed_at = (event.get("payload") or {}).get("env")
    return aimed_at is None or str(aimed_at) == env


def _attempts_ceiling(context: dict[str, Any], actor: Any) -> int | None:
    """The policy's ceiling on this transition's attempts (`attempts.by_role`, S27-08); `None`
    when the policy names none for the role, for a job (no role: ADR 0056), or for a history
    recorded before `POLICY_ATTEMPTS_CEILING`."""
    ceilings = context.get("attempts_ceilings")
    if not ceilings or actor is None or not workflow.patched(POLICY_ATTEMPTS_CEILING):
        return None
    ceiling = ceilings.get(str(actor.role))
    return int(ceiling) if ceiling else None
