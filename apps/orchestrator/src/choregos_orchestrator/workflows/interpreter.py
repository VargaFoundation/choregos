# SPDX-License-Identifier: Apache-2.0
"""`WorkflowInterpreter` : exécute le DSL épinglé pour un ticket (docs/plan/02 §2.1).

Le workflow ne fait **aucune** entrée/sortie et n'appelle **jamais** un modèle : il décide,
les activités agissent. Toute la logique de décision vient de `choregos_core.WorkflowEngine`,
ce qui la rend testable sans Temporal et rejouable sans surprise.
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

from .activity_names import named
from .planification import executer_activite

#: Attente maximale d'un run EN FILE (six heures), la même valeur que
#: `activities.stage.FILE_MAX_MINUTES` — écrite ici parce qu'un workflow n'importe pas
#: le module des activités, et parce qu'elle entre dans des bornes Temporal.
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
#: Pour une activité qui ne fait que SCRUTER (`await_run`) : idempotente, donc rejouable.
#: Elle tournait sans retry, et un redémarrage du worker pendant un run — un `helm upgrade`,
#: banc du 2026-09-24 — la faisait expirer sur son heartbeat et tuait le ticket.
POLL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=20,
)
HISTORY_THRESHOLD = 20_000
#: `migrate` reçoit la définition cible dans le signal, la consigne et ne tue plus le ticket
#: (ADR 0031, S16-05). Les historiques d'avant ce marqueur rejouent l'ancien chemin.
MIGRATION_PAR_DEFINITION = "migration-par-definition"  # lang: fr-ok — a patch id is never renamed
#: Rendu par `_wait_external` quand une migration le réveille : l'état ne bouge pas, la boucle migre.
_MIGRATION = "\x00migration"
#: Une transition système propose une action gouvernée, à date (S20-05). Les historiques d'avant
#: ce marqueur n'en avaient pas : leurs transitions système ne connaissent que des garanties.
ACTIONS_DE_TRANSITION = "actions-de-transition"  # lang: fr-ok — a patch id is never renamed
#: Le filet d'une action qui attend : relue en base à ce rythme, au cas où `action_settled` se perd.
RELECTURE_D_UNE_ACTION = timedelta(hours=6)
#: Une transition humaine peut être une TÂCHE : un formulaire, une attestation (S20-06). Avant ce
#: marqueur, toute transition humaine demandait une approbation.
TACHES_HUMAINES = "taches-humaines"  # lang: fr-ok — a patch id is never renamed
#: Le train porte au ticket la fin de SON départ, et l'approbation que son workflow exige (ADR 0041,
#: S21-21). Avant ce marqueur, le ticket prenait pour sien tout événement `cd.*`, quel qu'en soit
#: l'environnement : le staging terminé valait la prod.
TRAIN_PAR_ENVIRONNEMENT = "train-par-environnement"  # lang: fr-ok — a patch id is never renamed
#: Un ticket garé par une question ou une escalade REPREND (#313) : une réponse, ou « Replay the
#: stage », le ramène à l'état d'où il est parti, avec un budget de tentatives neuf. Avant ce
#: marqueur, `on_answer: resume` était déclaré et rien ne le jouait : seul l'abandon en sortait.
REPRISE_APRES_ESCALADE = "resume-after-escalation"
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
#: a suspected prompt injection — still fails the interpreter, as before.
CONFIGURATION_REFUSALS = frozenset(
    {
        "ModelResolutionError",
        "AgentIndisponible",  # lang: fr-ok — an error type the activities already raise
        "InstructionsInvalides",  # lang: fr-ok — an error type the activities already raise
        "SkillPendingReview",
        "SkillAbsente",  # lang: fr-ok — an error type the activities already raise
        "KnowledgeRefused",
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
    """Ce qu'il faut pour démarrer (ou reprendre) l'interprétation d'un ticket."""

    project_id: str
    project_slug: str
    work_item_id: str
    tracker_key: str
    resume_from: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    history_threshold: int = HISTORY_THRESHOLD
    #: Les tentatives déjà comptées quand le ticket a repris, par transition (voir `_essais`).
    attempts_reset: dict[str, int] = field(default_factory=dict)
    #: D'où reprendre un ticket garé : `[état garé, état d'origine, transition]`.
    parked: list[str] | None = None
    #: The run whose sandbox is suspended while a person answers (S35-03), across a
    #: `continue_as_new`: `{run_id, key, until, answered}`.
    suspended: dict[str, Any] | None = None


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
    """Un workflow par ticket, ID déterministe `wi-<project>-<tracker_key>`."""

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
        self.workflow_override: dict[str, Any] | None = None
        self.state_mapping: dict[str, str] = {}
        #: Le signal `migrate` entier : la définition, son identifiant, le mapping.
        self.migration: dict[str, Any] = {}
        self.migrations: int = 0
        self.findings_count: int = 0
        #: Les champs du ticket, que lit une date d'action ; `fields_changed` les remplace.
        self.champs: dict[str, Any] = {}
        self.version_des_champs: int = 0
        #: Les actions réglées que leur `action_settled` a annoncées, par identifiant.
        self.actions_reglees: dict[str, str] = {}
        #: Ce que le ticket attend : la date d'une action, l'action elle-même.
        self.attente: str | None = None
        self.action_en_cours: str | None = None
        #: The failure signature of each stage's last attempt (S27-07).
        self.failure_signatures: dict[str, str] = {}
        #: Le ticket garé (question, escalade) et d'où il reprendra : `(garé, origine, transition)`.
        self.reprise: tuple[str, str, str] | None = None
        #: Les tentatives d'avant la dernière reprise : un numéro de run ne se réutilise pas (il
        #: fait l'identifiant du run), le budget de tentatives, lui, repart de zéro.
        self.remises: Counter[str] = Counter()
        #: « Replay the stage » demandé (`rerun_stage`), pas encore joué.
        self.relance: bool = False
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

    # ───────────────────────── signaux et requêtes ─────────────────────────

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
        """Les champs ont changé (S20-05) : une date d'action se recalcule, son minuteur se réarme."""
        self.champs = dict(payload.get("fields") or {})
        self.version_des_champs += 1

    @workflow.signal
    def child_settled(self, payload: dict[str, Any]) -> None:
        """A child work item this one opened is closed (S38-12): the join re-reads its children."""
        self.children_settled += 1

    @workflow.signal
    def action_settled(self, payload: dict[str, Any]) -> None:
        """Une action de transition est réglée — réussie, échouée, rejetée (S20-05)."""
        self.actions_reglees[str(payload.get("action_id"))] = str(payload.get("status"))

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
            self.relance = True
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
            "waiting_until": self.attente,
            "action": self.action_en_cours,
        }

    # ───────────────────────── boucle principale ─────────────────────────

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = InterpreterInput(**payload)
        try:
            return await self._run(params, payload)
        except (asyncio.CancelledError, workflow.ContinueAsNewError):
            raise
        except Exception as exc:
            # Le workflow meurt : le dire AVANT de mourir. Sans cette trace, le ticket reste
            # dans son état d'origine et rien — ni écran, ni événement — ne distingue « il
            # attend » de « il est mort ». Le banc du 2026-09-24 a perdu deux tickets ainsi.
            with contextlib.suppress(Exception):
                await executer_activite(
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
        self.remises.update(params.attempts_reset)
        if params.parked:
            self.reprise = (params.parked[0], params.parked[1], params.parked[2])
        self.suspension = dict(params.suspended) if params.suspended else None
        self.project_id = params.project_id
        self.work_item_id = params.work_item_id
        self.cost_usd = params.cost_usd

        context = await executer_activite(
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
                if workflow.patched(MIGRATION_PAR_DEFINITION):
                    engine = await self._migrer(params, engine)
                else:
                    engine = self._migrate(engine)
                continue

            transition = engine.select_transition(self.state, self.last_outcome)
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
            elif kind == "human":
                decision = await self._wait_human(params, engine, transition)
            elif kind == "train":
                decision = await self._run_train(params, engine, transition)
            else:
                decision = await self._run_system(params, engine, transition)

            if decision is None:
                continue
            self._noter_la_reprise(engine, transition, kind, decision)
            self.state = decision.next_state
            await self._mirror(params, decision.reason)

            if workflow.info().get_current_history_length() > params.history_threshold:
                workflow.continue_as_new(
                    {
                        **payload,
                        "resume_from": self.state,
                        "attempts": dict(self.attempts),
                        "cost_usd": self.cost_usd,
                        "attempts_reset": dict(self.remises),
                        "parked": list(self.reprise) if self.reprise else None,
                        "suspended": self.suspension,
                    }
                )

        await self._release_suspension()  # the work item ends: no sandbox left waiting
        await executer_activite(
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
        if self.version_des_champs == 0:  # a `fields_changed` that arrived before the read is newer
            self.champs = dict(context.get("fields") or {})

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

    # ───────────────────────── étapes ─────────────────────────

    async def _run_stage(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        if engine.agent_of(transition) is None and engine.job_of(transition) is None:
            return engine.retry_or_escalate(
                transition, self._essais(transition.key), "acteur agent introuvable"
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
            prepare = executer_activite(
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
                started = await executer_activite(
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
        awaited = await executer_activite(
            stage_activities.await_run,
            {
                "project_id": params.project_id,
                "run_id": run_id,
                "timeout_minutes": int(budget["max_minutes"]) + 10,
            },
            start_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 20),
            # Borne GLOBALE, toutes tentatives comprises : sans elle, vingt reprises d'une
            # attente de deux heures feraient un ticket qui ne meurt jamais.
            schedule_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 90),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=POLL_RETRY,
        )
        result = StageResult.model_validate(awaited["result"])

        spend = await executer_activite(
            stage_activities.collect_spend,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        if "ticket_cost_usd" in spend and workflow.patched(TICKET_SPEND_FROM_LEDGER):
            self.cost_usd = float(spend["ticket_cost_usd"])  # the ledger: models AND tools
        else:
            self.cost_usd += float(spend.get("cost_usd", 0.0))
        await executer_activite(
            stage_activities.record_run_outcome,
            {"project_id": params.project_id, "run_id": run_id, "result": awaited["result"]},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        # Le diff du run est ce que le front AFFICHE — pas ce dont le ticket dépend. Un échec
        # ici (SCM injoignable, projet sans dépôt sur une image en retard) ne doit pas tuer une
        # étape qui vient de réussir : c'est arrivé à RH-1 le 2026-09-24.
        try:
            await executer_activite(
                scm_activities.collect_run_artifacts,
                {"project_id": params.project_id, "work_item_id": params.work_item_id, "run_id": run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=NO_RETRY,
            )
        except ActivityError as exc:
            workflow.logger.warning("diff du run non collecté : %s", message_de(exc))
        self.current_run = None
        self.last_run = run_id  # les transitions `system` évaluent leurs gates sur ce run

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
                self._essais(key),
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

    # ──────────────────── a sandbox kept across an answer (S35-03) ────────────────────

    async def _suspend(self, params: InterpreterInput, run_id: str, key: str) -> None:
        """The agent asked a question: its sandbox waits, suspended, for the answer."""
        done = await executer_activite(
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
        await executer_activite(
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
        resumed = await executer_activite(
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

        raw = await executer_activite(
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
        """Attend les événements externes (CI, review) puis réévalue, jusqu'au timeout."""
        from choregos_core import GateOutcome

        deadline = timedelta(hours=transition.timeout_hours or 24)
        waited = timedelta()
        step = timedelta(minutes=5)
        while waited < deadline:
            received = await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                self._absorb(event)
            raw = await executer_activite(
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
            if received is False:  # wait_condition a expiré sans signal
                continue
        return [GateOutcome(name=name, passed=False, detail="timed out") for name in names]

    def _absorb(self, event: dict[str, Any]) -> None:
        """Traduit un événement entrant en `last_outcome` pour la prochaine sélection."""
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

    # ───────────────────────── humain ─────────────────────────

    async def _wait_human(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        actor = engine.human_of(transition)
        sla_hours = (actor.sla_hours if actor else None) or 24
        tache = transition.task is not None and workflow.patched(TACHES_HUMAINES)
        if tache:
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
                lambda: bool(self.decisions) or self.stopped or self._relance_jouable(),
                min(step, timeout - waited),
            )
            if self.stopped:
                return None
            if self._suspension_left() == timedelta(0):
                # `sandbox.suspend_max_hours` passed: the sandbox goes (the executor's platform
                # deletes it at that instant anyway), and the answer will replay the stage.
                await self._release_suspension()
            if await self._replay_requested():
                return await self._reprendre("replay requested", self.pending_request, engine=engine)
            if self.decisions:
                decision = self.decisions.popleft()
                if self._reponse_qui_reprend(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    return await self._reprendre(
                        "answered", decision.get("request_id"), decision, self.pending_request, engine=engine
                    )
                await executer_activite(
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
                if tache and approved and decision.get("values"):
                    # Les valeurs de la tâche sont des champs : l'API les a déjà écrits, une date
                    # d'action qui en dépend se recalcule ici.
                    self.champs = {**self.champs, **dict(decision["values"])}
                    self.version_des_champs += 1
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

        await executer_activite(
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
        # L'enveloppe de planification rend un `Any` là où `execute_activity` avait une surcharge
        # typée : le passage par un nom annoté garde la promesse de la signature.
        demande: dict[str, Any] = await executer_activite(
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
        return demande

    async def _request_scope_change(self, params: InterpreterInput, transition: Any, request: Any) -> None:
        await self._create_request(
            params,
            transition,
            kind="scope_change",
            payload={"paths": list(request.paths), "justification": request.justification},
        )

    # ───────────────────────── système et train ─────────────────────────

    async def _run_system(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1

        if transition.spawn is not None and workflow.patched(FAN_OUT):
            return await self._run_spawn(params, engine, transition)
        if transition.action is not None and workflow.patched(ACTIONS_DE_TRANSITION):
            return await self._run_action(params, engine, transition, attempt + 1)

        # Ce que la plateforme fait s'ÉCRIT (`does`, #175) ; un workflow publié avant le portait dans le
        # nom de l'état visé, et se lit encore ainsi : une histoire enregistrée se rejoue à l'identique.
        effet = effet_de_la_transition(transition)
        if effet == "open_pr":
            try:
                await executer_activite(
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
        if effet == "merge_pr" and all(not o.blocking and not o.pending for o in outcomes):
            await executer_activite(
                scm_activities.enqueue_merge,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
        return engine.after_gates(transition, outcomes, self._essais(key))

    async def _run_action(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, tentative: int
    ) -> Any:
        """Une transition à action (ADR 0035, S20-05) : attendre sa date, la proposer, attendre
        qu'elle soit RÉGLÉE — décidée par une personne ou par la politique, puis jouée par son
        `ActionWorkflow`, jamais ici —, et juger `action_succeeded` avec les autres garanties."""
        spec = transition.action
        if spec.not_before and not await self._attendre_la_date(spec.not_before):
            return None  # arrêté, ou une migration à appliquer : la boucle s'en charge
        proposee = await executer_activite(
            named(
                action_activities.propose_transition_action, action_activities.proposer_l_action_de_transition
            ),
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition": transition.key,
                "attempt": tentative,
                "workflow_id": workflow.info().workflow_id,
                "action": spec.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        if proposee.get("refus"):
            refus = GateOutcome(
                "action_succeeded", False, detail=f"the action cannot be proposed: {proposee['refus']}"
            )
            return engine.after_gates(transition, [refus], self._essais(transition.key))
        action_id, statut = str(proposee["action_id"]), str(proposee["status"])
        self.action_en_cours = action_id
        while statut not in ACTION_REGLEE and not self.stopped:
            await wait_signal(
                lambda: action_id in self.actions_reglees or self.stopped, RELECTURE_D_UNE_ACTION
            )
            if action_id in self.actions_reglees:
                statut = self.actions_reglees.pop(action_id)
            elif not self.stopped:
                relue = await executer_activite(
                    named(action_activities.action_state, action_activities.etat_de_l_action),
                    action_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                statut = str(relue["status"])
        self.action_en_cours = None
        if self.stopped:
            return None
        autres = transition.model_copy(
            update={"gates": [g for g in transition.gates if g.name != "action_succeeded"]}
        )
        outcomes = await self._gates(params, autres, self.last_run or "")
        return engine.after_action(transition, statut, outcomes, self._essais(transition.key))

    async def _run_spawn(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        """A fan-out and its join (S38-12, ADR 0057): open the children, then wait until every one is
        settled — woken by `child_settled`, re-reading them every `CHILDREN_REREAD` in case a signal
        is lost —, and judge the join with the transition's other gates. Opening is idempotent (the
        children's keys are the parent's with a rank): a migration or a retry finds them again."""
        spec = transition.spawn.model_dump(mode="json")
        opened = await executer_activite(
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
            return engine.after_gates(transition, [refused], self._essais(transition.key))
        children = list(opened["children"])
        while not self.stopped and self.workflow_override is None:
            seen = self.children_settled
            state = await executer_activite(
                children_activities.children_state,
                {"work_item_id": params.work_item_id, "children": children, "spawn": spec},
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=DEFAULT_RETRY,
            )
            if not state["pending"]:
                break
            self.attente = f"{len(state['pending'])} of {len(children)} child work items not settled"
            await wait_signal(
                lambda seen=seen: (
                    self.children_settled != seen or self.stopped or self.workflow_override is not None
                ),
                CHILDREN_REREAD,
            )
        self.attente = None
        if self.stopped or self.workflow_override is not None:
            return None  # stopped, or a migration to apply: the loop sees to it
        joined = GateOutcome("children_settled", bool(state["passed"]), detail=str(state["detail"]))
        outcomes = await self._gates(params, transition, self.last_run or "")
        return engine.after_gates(transition, [joined, *outcomes], self._essais(transition.key))

    async def _attendre_la_date(self, expression: str) -> bool:
        """Attend `not_before`, RECALCULÉ à chaque changement des champs : déplacer la date
        réarme le minuteur, l'avancer sous l'heure présente le fait partir. Un champ absent ou
        illisible : on attend qu'il soit renseigné — on ne part pas sans date. Faux quand le
        ticket s'arrête ou qu'une migration l'interrompt."""
        while not self.stopped and self.workflow_override is None:
            vue = self.version_des_champs
            try:
                cible = echeance(expression, self.champs)
            except DateIllisible:
                cible = None
            maintenant = workflow.now()
            if cible is not None and cible <= maintenant:
                if self.paused:  # la date est passée pendant une pause : on attend la reprise
                    await workflow.wait_condition(
                        lambda: not self.paused or self.stopped or self.workflow_override is not None
                    )
                    continue
                self.attente = None
                return True
            self.attente = cible.isoformat() if cible is not None else f"{expression} (unknown)"
            await wait_signal(
                lambda vue=vue: (
                    self.version_des_champs != vue or self.stopped or self.workflow_override is not None
                ),
                (cible - maintenant) if cible is not None else None,
            )
        self.attente = None
        return False

    async def _run_train(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        """Embarque le ticket, puis attend que SON train ait livré — `finish_release` et `rollback` le
        préviennent (ADR 0041). L'approbation que le workflow exige voyage avec lui jusqu'au départ."""
        env = env_du_train(transition)
        par_environnement = workflow.patched(TRAIN_PAR_ENVIRONNEMENT)
        embarquement: dict[str, Any] = {
            "project_id": params.project_id,
            "project_slug": params.project_slug,
            "work_item_id": params.work_item_id,
            "env": env,
        }
        if par_environnement:
            embarquement["approval"] = dict(approbation_du_train(engine.wf, transition))
        await executer_activite(
            signal_train,
            embarquement,
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
                if par_environnement and not _de_cet_environnement(event, env):
                    # Le staging terminé n'est pas la prod : un ticket qui prend deux trains de
                    # suite recevait le second pour livré dès que le premier l'était.
                    self._absorb(event)
                    continue
                if event.get("type") in {"cd.rollout.completed", "cd.app.synced"}:
                    return engine.after_train(transition, ok=True)
                if event.get("type") in {"cd.rollout.aborted", "cd.app.degraded"}:
                    return engine.after_train(transition, ok=False)
                self._absorb(event)
            waited += step
        return engine.after_train(transition, ok=False)

    # ───────────────────────── reprise d'un ticket garé ─────────────────────────

    def _noter_la_reprise(self, engine: WorkflowEngine, transition: Any, kind: str, decision: Any) -> None:
        """Une étape qui gare le ticket — question, escalade — dit d'où il reprendra."""
        if (
            kind != "human"
            and decision.next_state != transition.from_
            and (decision.escalated or decision.next_state == engine.question_state())
        ):
            self.reprise = (decision.next_state, transition.from_, transition.key)

    def _essais(self, cle: str) -> int:
        """Les tentatives qui comptent pour `max_attempts` : celles d'après la dernière reprise."""
        return self.attempts[cle] - self.remises[cle]

    def _est_gare(self) -> bool:
        return self.reprise is not None and self.reprise[0] == self.state

    def _relance_jouable(self) -> bool:
        """« Replay the stage » ne joue que sur un ticket garé ; ailleurs, il est oublié."""
        if self.relance and not self._est_gare() and not self.resume_lookup_pending:
            self.relance = False
        return self.relance

    def _look_up_resume(self) -> None:
        """A request to resume a work item this interpreter did not park — it was started again
        from the database (#497): where it resumes is to be read before the request plays."""
        if self._est_gare():
            return
        # The condition first, `patched` next — and one id per request (see the marker).
        self.resume_lookups += 1
        if workflow.patched(f"{ESCALATION_CONTEXT_AFTER_RESTART}-{self.resume_lookups}"):
            self.resume_lookup_pending = True

    async def _replay_requested(self) -> bool:
        """`_relance_acceptee`, once a restarted interpreter has read where its work item resumes —
        which an answer waiting in `decisions` needs as well."""
        if self.resume_lookup_pending:
            await self._read_the_parking()
        return self._relance_acceptee()

    async def _read_the_parking(self) -> None:
        """Rebuilds `reprise` from what the escalation left in the database (S22-41), and the
        transition's attempts: the replayed stage gets a new run number, not the failed run's."""
        self.resume_lookup_pending = False
        if self._est_gare():
            return
        found = await executer_activite(
            escalation_context,
            {"project_id": self.project_id, "work_item_id": self.work_item_id, "state": self.state},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        parked = found.get("parked")
        if not parked or parked[0] != self.state:
            return
        self.reprise = (str(parked[0]), str(parked[1]), str(parked[2]))
        key = self.reprise[2]
        self.attempts[key] = max(self.attempts[key], int(found.get("attempts") or 0))

    def _relance_acceptee(self) -> bool:
        """La relance demandée joue-t-elle ? Sinon elle est OUBLIÉE : laissée en place, elle tenait
        vraie la condition d'attente, et `_wait_human` consommait son délai d'une heure fictive par
        tour — sur le locataire dev, le 09/10, une demande d'abandon recréée toutes les 4 s.

        C'est le cas d'un historique où `patched` a déjà répondu non : le SDK MÉMORISE la réponse
        pour toute l'exécution, et un ticket qui avait reçu une réponse avant le marqueur ne prend
        jamais le nouveau chemin. Il faut alors le réinitialiser avant cette réponse."""
        if self._relance_jouable() and workflow.patched(REPRISE_APRES_ESCALADE):
            return True
        self.relance = False
        return False

    def _reponse_qui_reprend(self, decision: dict[str, Any]) -> bool:
        """Une RÉPONSE à une question, sur un ticket garé : `on_answer: resume`."""
        return (
            decision.get("kind") == "question"
            and self._est_gare()
            and workflow.patched(REPRISE_APRES_ESCALADE)
        )

    def _rerun_allowance(self, engine: WorkflowEngine, key: str) -> None:
        """A rerun leaves the stage ONE attempt before its cap (S27-13): the replayed stage plays
        once, and escalates again if it fails. Attempts spent before count, whatever the number of
        reruns, so `max_attempts` bounds the stage plus one attempt per explicit rerun."""
        transition = next((t for t in engine.wf.transitions if t.key == key), None)
        if transition is None:
            self.remises[key] = self.attempts[key]
            return
        allowed = engine.max_attempts(transition)
        ceiling = _attempts_ceiling({"attempts_ceilings": self.ceilings}, engine.agent_of(transition))
        if ceiling is not None:
            allowed = min(allowed, ceiling)
        self.remises[key] = max(self.remises[key], self.attempts[key] - (allowed - 1))

    async def _reprendre(
        self, pourquoi: str, *demandes: Any, engine: WorkflowEngine | None = None
    ) -> Decision:
        """Brings the parked work item back to the state it left: pending requests are closed (the
        abandon offered is moot), and the stage gets one more attempt, not a new allowance."""
        assert self.reprise is not None
        _, origine, cle = self.reprise
        reponse = next((d for d in demandes if isinstance(d, dict)), None)
        for demande in dict.fromkeys(d for d in demandes if isinstance(d, str) and d):
            await executer_activite(
                tracker_activities.close_human_request,
                {
                    "request_id": demande,
                    "decided_by": (reponse or {}).get("decided_by", "the platform"),
                    "decision": reponse
                    if reponse and reponse.get("request_id") == demande
                    else {"approved": False, "superseded": f"the work item resumed ({pourquoi})"},
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
        self.pending_request = None
        self.relance = False
        self.reprise = None
        # The condition first, `patched` next: a rerun asks its marker, an answer its own.
        marker = ANSWER_GRANTS_ONE_ATTEMPT if reponse is not None else RERUN_GRANTS_ONE_ATTEMPT
        if engine is not None and workflow.patched(marker):
            self._rerun_allowance(engine, cle)
        else:
            self.remises[cle] = self.attempts[cle]
        return Decision(next_state=origine, reason=f"{pourquoi}: resumes at `{origine}`")

    # ───────────────────────── utilitaires ─────────────────────────

    async def _migrer(self, params: InterpreterInput, engine: WorkflowEngine) -> WorkflowEngine:
        """`migrate` : la définition cible arrive dans le signal, lue et vérifiée par l'API.

        L'épingle du ticket la suit (un `continue_as_new` la rechargera). Une migration devenue
        impossible — l'état a bougé depuis la vérification de l'API — laisse le ticket en vie, sur
        sa définition, et le consigne : `_migrate` le tuait en levant dans le workflow.
        """
        demande, self.migration, self.workflow_override = self.migration, {}, None
        self.migrations += 1
        info = workflow.info()
        consigne = {
            "project_id": params.project_id,
            "work_item_id": params.work_item_id,
            "cle": f"{info.workflow_id}/{info.run_id}/{self.migrations}",
            "from_state": self.state,
        }
        try:
            cible = Workflow.model_validate(demande.get("workflow") or {})
            etat = engine.can_migrate_to(cible, self.state, dict(demande.get("state_mapping") or {}))
        except ValueError as refus:  # une ValidationError de pydantic en est une
            await executer_activite(
                record_migration,
                {**consigne, "refus": str(refus)[:2000]},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            return engine
        await executer_activite(
            record_migration,
            {
                **consigne,
                "to_state": etat,
                "workflow_def_id": demande.get("workflow_def_id"),
                "workflow": f"{cible.metadata.name}@{cible.metadata.version}",
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self.state = etat
        await self._mirror(params, f"migrated to {cible.metadata.name}@{cible.metadata.version}")
        return WorkflowEngine(cible)

    def _migrate(self, engine: WorkflowEngine) -> WorkflowEngine:
        """Ancien chemin, gardé pour rejouer les historiques d'avant `MIGRATION_PAR_DEFINITION`."""
        override = self.workflow_override or {}
        self.workflow_override, self.migration = None, {}
        definition = override.get("workflow") or override.get("json") or override
        target = Workflow.model_validate(definition)
        new_engine = WorkflowEngine(target)
        self.state = WorkflowEngine(engine.wf).can_migrate_to(target, self.state, self.state_mapping)
        return new_engine

    async def _wait_external(self, engine: WorkflowEngine, context: dict[str, Any]) -> str | None:
        """État d'attente sans transition sortante : c'est le board humain qui décide.

        Une migration demandée la réveille aussi (`_MIGRATION`) : un ticket garé n'a pas d'autre
        point de décision, et attendrait sinon un événement qui ne viendra peut-être jamais. Un
        historique d'avant `MIGRATION_PAR_DEFINITION` ne se réveillait pas : il rejoue en attendant
        l'événement suivant, comme alors.
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
                        or self._relance_jouable()
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
                    await self._reprendre("replay requested", self.pending_request, engine=engine)
                ).next_state
            if self.migration and not (self.inbox or self.decisions or self.stopped):
                if workflow.patched(MIGRATION_PAR_DEFINITION):
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
                if self._reponse_qui_reprend(decision):
                    if self.suspension is not None:
                        self.suspension["answered"] = True
                    reprise = await self._reprendre(
                        "answered", decision.get("request_id"), decision, engine=engine
                    )
                    return reprise.next_state
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
        if self.reprise is not None and self._est_gare() and workflow.patched(ESCALATION_CONTEXT_STORED):
            mirrored["parked"] = list(self.reprise)  # where it resumes, for a restarted interpreter
        await executer_activite(
            tracker_activities.mirror_state,
            mirrored,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        await executer_activite(
            tracker_activities.update_status_comment,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )


async def wait_signal(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """`workflow.wait_condition` qui rend `False` sur expiration au lieu de lever.

    (Dans le SDK Temporal, un `wait_condition` qui expire lève `asyncio.TimeoutError` ;
    ici on veut distinguer « la condition est arrivée » de « le délai est écoulé ».)
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


def _de_cet_environnement(event: dict[str, Any], env: str) -> bool:
    """Un événement de déploiement compte pour le train qui attend s'il vise son environnement.
    Sans environnement dit — un événement porté à la main, d'avant ADR 0041 —, il compte."""
    vise = (event.get("payload") or {}).get("env")
    return vise is None or str(vise) == env


def _attempts_ceiling(context: dict[str, Any], actor: Any) -> int | None:
    """The policy's ceiling on this transition's attempts (`attempts.by_role`, S27-08); `None`
    when the policy names none for the role, for a job (no role: ADR 0056), or for a history
    recorded before `POLICY_ATTEMPTS_CEILING`."""
    ceilings = context.get("attempts_ceilings")
    if not ceilings or actor is None or not workflow.patched(POLICY_ATTEMPTS_CEILING):
        return None
    ceiling = ceilings.get(str(actor.role))
    return int(ceiling) if ceiling else None
