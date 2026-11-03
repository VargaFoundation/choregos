# SPDX-License-Identifier: Apache-2.0
"""A decision step (ADR 0121 §2): a decision model answers typed questions, in the orchestrator.

No pod, no sandbox, no agent loop: one activity, `decide`, which

1. refuses a step its project cannot run — a gateway without the `/v1/systemone` route
   (`decision_route_refusal`), a model the organisation's catalogue does not enable for `decide`
   (`ModelPolicy.check_decision`) — as a configuration refusal the interpreter escalates;
2. assembles the `state` from exactly the sources the step names (a missing one is absent, not an
   error), masks the secrets in it and runs the injection guard on it (ADR 0084);
3. mints a short-lived virtual key on the project's gateway, for the step's model only, and asks the
   model through that gateway — never directly (ADR 0004, ADR 0109);
4. writes the run and its `StageResult` — `outputs` the answers and their probabilities, `summary` the
   answers in one line — books the spend read back from the gateway, and revokes the key, whatever
   happened.

The run's id is the stage's (`<work item>-<transition>-<attempt>`, ADR 0008): a retried activity
whose run already holds a result reads it back, and neither asks the model again nor writes again.
An answer that cannot be used fails the run with `reason=invalid_decision`, no answer with
`reason=decision_unreachable`; the workflow's `on_fail` then applies, as after a failed agent run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

import structlog
from choregos_adapters.base import decision_route_refusal
from choregos_adapters.decisions import (
    DecisionCall,
    DecisionReply,
    DecisionUnreachableError,
    InvalidDecisionError,
)
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_api.db.models import Run
from choregos_api.services import DecidedBy, persist_event, record_decisions, record_transition_taken
from choregos_api.services.model_catalogue import load_policy
from choregos_contracts import DecisionActor, EventType, StageResult, StageStatus
from choregos_contracts.workflow import CONFIDENCE_SUFFIX
from choregos_core import utcnow
from choregos_core.decision_ledger import Answered
from choregos_core.llm_guardrail import mask
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import get_settings
from .base import db, load_work_item, project_bundle, workflow_du_ticket
from .bilan import book_spend, ticket_view
from .garde import guard_sources
from .run_key import mint_key, record_step_key, revoke

logger = structlog.get_logger("choregos.decision")

#: What `run.backend` says of a decision step: no agent backend runs it.
DECISION_BACKEND = "decision"
#: The non-retryable error type of a step its project cannot run; the interpreter escalates it as
#: a configuration refusal (`CONFIGURATION_REFUSALS`).
DECISION_REFUSED = "DecisionRefused"
#: The run's failure reasons (ADR 0121 §2).
INVALID_DECISION = "invalid_decision"
DECISION_UNREACHABLE = "decision_unreachable"
#: The cap of a decision's key: a few milliseconds of a CPU model cost a fraction of a cent. The
#: role's stage budget lowers it, never raises it.
DECISION_KEY_BUDGET_USD = 0.05
#: How long the key lives at the gateway if the activity dies before revoking it (its row says the same).
DECISION_KEY_TTL_S = 300
#: The activity's time beyond the model's own timeout: the database, the key, the spend.
DECISION_MARGIN_S = 60
#: A decision step's records in the ledger (ADR 0121 §6), beside an agent's declared confidence.
DECISION_MODEL_SOURCE = "decision_model"


def decision_run_id(work_item_id: str, transition_id: str, attempt: int) -> str:
    """The stage's run id (ADR 0008), the same scheme as `prepare_stage`'s."""
    return f"{work_item_id}-{transition_id}-{attempt}"


def decision_request(
    *,
    project_id: str,
    work_item_id: str,
    transition: Any,
    decision: DecisionActor,
    attempt: int,
    last_outputs: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The `decide` payload, built by the interpreter (deterministic) and by the local engine.

    Of the last stage's outputs, only those the step names (`outputs.<name>`) travel: the history
    holds what the step reads, never a whole specification it does not.
    """
    named = [source.split(".", 1)[1] for source in decision.state if source.startswith("outputs.")]
    outputs = dict(last_outputs or {})
    return {
        "project_id": project_id,
        "work_item_id": work_item_id,
        "transition_id": transition.key,
        "from_state": transition.from_,
        "to_state": transition.to,
        "actor": transition.by or "",
        "attempt": attempt,
        "decision": decision.model_dump(mode="json"),
        "outputs": {name: outputs[name] for name in named if outputs.get(name) is not None},
    }


@activity.defn(name="decide")
async def decide(payload: dict[str, Any]) -> dict[str, Any]:
    """Asks the step's decision model through the project's gateway; see the module."""
    decision = DecisionActor.model_validate(payload["decision"])
    run_id = decision_run_id(payload["work_item_id"], payload["transition_id"], int(payload["attempt"]))
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        structlog.contextvars.bind_contextvars(
            project=bundle.slug, work_item=item.tracker_key, run_id=run_id, stage=str(decision.role)
        )
        run = await session.get(Run, run_id)
        if run is not None and run.result is not None:
            # A retried activity whose first attempt wrote its result: read back, never asked again.
            return await _decided(session, bundle, run, settings, reused=True)
        await _refuse_what_cannot_run(session, bundle, decision)
        state = await assemble_state(bundle, item, decision.state, payload.get("outputs") or {})
        state, masked = mask_state(state)
        await guard_sources(bundle, item, run_id, str(decision.role), guard_texts(state))

        run = run or Run(id=run_id, work_item_id=item.id, project_id=bundle.project.id)
        run.transition_id = payload["transition_id"]
        run.stage_role = str(decision.role)
        run.attempt = int(payload["attempt"])
        run.actor = payload.get("actor") or None
        run.backend = DECISION_BACKEND
        run.model = decision.model
        run.status = "running"
        run.started_at = utcnow()
        run.playbook_checksum = decision_ref(decision)
        run.allowed_paths = []
        run.initiated_by = item.initiated_by
        session.add(run)
        await session.flush()
        await persist_event(
            session,
            EventType.RUN_STARTED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=run_id,
            run_id=run_id,
            stage=str(decision.role),
            backend=DECISION_BACKEND,
            model=decision.model,
            # What left for the gateway, by name; the values stay in the step, never in an event.
            sent=sorted(state),
            secrets_masked=masked,
        )
        result = await _ask_through_the_gateway(
            session, bundle, item, run, decision, state, settings, step=str(payload.get("from_state") or "")
        )
        await persist_event(
            session,
            EventType.RUN_FINISHED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=run_id,
            run_id=run_id,
            stage=str(decision.role),
            status=run.status,
            **({"reason": result.reason} if result.reason else {}),
        )
        return await _decided(session, bundle, run, settings, reused=False)


@activity.defn(name="note_transition_taken")
async def note_transition_taken(payload: dict[str, Any]) -> dict[str, Any]:
    """The transition the workflow selected after a recorded step: the state it leads to, written on the
    step's ledger records (ADR 0121 §6). The interpreter calls it once, behind its marker; a retry
    writes nothing more, a record that knows its transition keeps it."""
    async with db() as session:
        noted = await record_transition_taken(session, str(payload["run_id"]), str(payload["transition"]))
    return {"noted": noted}


async def _ask_through_the_gateway(
    session: Any,
    bundle: Any,
    item: Any,
    run: Run,
    decision: DecisionActor,
    state: dict[str, Any],
    settings: Any,
    *,
    step: str,
) -> StageResult:
    """Mint the step's key, ask, write the result and its ledger records, book the spend; the key is
    revoked in any case."""
    budget = min(DECISION_KEY_BUDGET_USD, bundle.engine.stage_usd(str(decision.role), item.size))
    try:
        key = await mint_key(
            bundle,
            {
                "project": bundle.slug,
                "work_item": item.tracker_key,
                "run_id": run.id,
                "transition": run.transition_id,
                "role": run.stage_role,
                "attempt": run.attempt,
                "backend": DECISION_BACKEND,
            },
            budget_usd=budget,
            ttl_s=DECISION_KEY_TTL_S,
            models=[decision.model],
        )
    except ConfigurationError as refusal:  # the policy masks secrets on the LLM path, the gateway cannot
        raise ApplicationError(str(refusal), type=DECISION_REFUSED, non_retryable=True) from refusal
    except UpstreamError as error:
        return _write(
            run, StageResult.failure(f"no key for the decision step: {error}"[:4000], DECISION_UNREACHABLE)
        )
    record_step_key(session, bundle, run, key)
    released = False
    try:
        call = DecisionCall(
            # The gateway a run of this project is given (ADR 0109): the key is only good there.
            base_url=getattr(bundle, "gateway_url", None) or settings.gateway_url,
            key=str(key.key),
            model=decision.model,
            state=state,
            questions=decision.questions,
            timeout_s=float(decision.timeout_seconds),
        )
        reply: DecisionReply | None = None
        try:
            reply = await bundle.adapters.decisions.decide(call)
            result = answered(decision, reply)
        except InvalidDecisionError as refused:
            result = StageResult.failure(
                f"the decision model's answer is refused: {refused}"[:4000], INVALID_DECISION
            )
        except DecisionUnreachableError as error:
            result = StageResult.failure(
                f"the decision model did not answer: {error}"[:4000], DECISION_UNREACHABLE
            )
        _write(run, result)
        if reply is not None and result.status is StageStatus.DONE:
            # The ledger, in the transaction of the result: a retry that reads the result back finds
            # them written, and a replayed insert adds nothing (ADR 0121 §6).
            await record_decisions(
                session, await _decided_by(session, bundle, item, run, decision, step, reply), _answers(reply)
            )
        await session.flush()
        try:
            await book_spend(session, bundle, run, settings)  # reads the spend, then revokes the key
            released = True
        except Exception as error:  # the answer is kept: asking again would cost it twice
            logger.warning("decision spend not booked", run_id=run.id, error=str(error)[:300])
    finally:
        if not released:
            await revoke(bundle, key.key_id)
    return result


async def _decided_by(
    session: Any, bundle: Any, item: Any, run: Run, decision: DecisionActor, step: str, reply: DecisionReply
) -> DecidedBy:
    """What every record of this decision shares. The model's version is the model the gateway says
    answered, when it names another than the catalogue entry asked: the entry is an alias, and the
    calibration of one model does not carry over to the next."""
    workflow = await workflow_du_ticket(session, item)
    answered_by = reply.model if reply.model and reply.model != decision.model else None
    return DecidedBy(
        org_id=bundle.project.org_id,
        project_id=bundle.project.id,
        project_slug=bundle.slug,
        work_item_id=item.id,
        work_item_key=item.tracker_key,
        run_id=run.id,
        workflow=workflow.metadata.name,
        workflow_version=workflow.metadata.version,
        step=step or str(run.transition_id or ""),
        transition_id=run.transition_id,
        source=DECISION_MODEL_SOURCE,
        model=decision.model,
        model_version=answered_by,
        decided_at=run.ended_at or utcnow(),
    )


def _answers(reply: DecisionReply) -> list[Answered]:
    return [Answered(name, answer.value, answer.probability) for name, answer in reply.answers.items()]


def _write(run: Run, result: StageResult) -> StageResult:
    run.result = result.model_dump(mode="json", by_alias=True)
    run.status = "succeeded" if result.status is StageStatus.DONE else "failed"
    run.ended_at = utcnow()
    return result


async def _decided(session: Any, bundle: Any, run: Run, settings: Any, *, reused: bool) -> dict[str, Any]:
    """What the interpreter needs: the run, its result, what the work item has cost."""
    view: dict[str, Any]
    if run.spend_collected:
        view = await book_spend(session, bundle, run, settings)  # counted once: only read here
    else:
        spent = await ticket_view(session, bundle, run.work_item_id)
        view = {"cost_usd": float(run.cost_usd or 0.0), **spent}
    return {
        "run_id": run.id,
        "result": run.result,
        "reused": reused,
        "cost_usd": float(view.get("cost_usd", 0.0)),
        "ticket_cost_usd": float(view.get("ticket_cost_usd", 0.0)),
        "ticket_budget_usd": float(view.get("ticket_budget_usd", 0.0)),
    }


async def _refuse_what_cannot_run(session: Any, bundle: Any, decision: DecisionActor) -> None:
    """A gateway without the decision route, a model the catalogue does not enable for `decide`:
    the configuration has to change, so the refusal is not retried and the work item escalates."""
    refusal = decision_route_refusal(bundle.adapters.gateway)
    if refusal is None:
        catalogue = await load_policy(session, bundle.project.org_id)
        refusal = catalogue.check_decision(decision.model)
    if refusal is not None:
        logger.info("decision step refused", reason=refusal)
        raise ApplicationError(refusal, type=DECISION_REFUSED, non_retryable=True)


async def assemble_state(
    bundle: Any, item: Any, sources: list[str], outputs: Mapping[str, Any]
) -> dict[str, Any]:
    """The state sent to the model: exactly the sources the step names, under their names.

    `title`, `description` (the work item's body), `labels` and `type` (read from its tracker),
    `fields.<name>` (its fields) and `outputs.<name>` (the last stage's). A source that is empty or
    missing is left out — the model is asked about what there is, and the step does not fail.
    """
    tracked: Any = None
    if {"labels", "type"} & set(sources):
        try:
            tracked = await bundle.adapters.tracker.fetch_item(item.tracker_key)
        except Exception as error:  # what the tracker cannot say is absent, as a missing field
            logger.warning("tracker not read for a decision", error=str(error)[:300])
    fields = dict(item.fields or {})
    state: dict[str, Any] = {}
    for source in sources:
        value: Any
        if source == "title":
            value = item.title
        elif source == "description":
            value = item.body_snapshot
        elif source == "labels":
            value = list(getattr(tracked, "labels", None) or [])
        elif source == "type":
            value = getattr(tracked, "item_type", None)
        elif source.startswith("fields."):
            value = fields.get(source.split(".", 1)[1])
        else:  # `outputs.<name>`, the only other source the contract allows
            value = outputs.get(source.split(".", 1)[1])
        if value not in (None, "", [], {}):
            state[source] = value
    return state


def mask_state(state: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """The state with every high-precision secret masked (ADR 0084), and how many were.

    Always, whatever the policy: a decision model never needs a secret to answer a question.
    """

    def walk(value: Any) -> tuple[Any, int]:
        if isinstance(value, str):
            return mask(value)
        if isinstance(value, list):
            pairs = [walk(element) for element in value]
            return [masked for masked, _ in pairs], sum(count for _, count in pairs)
        if isinstance(value, dict):
            pairs_by_key = {key: walk(element) for key, element in value.items()}
            return {key: pair[0] for key, pair in pairs_by_key.items()}, sum(
                p[1] for p in pairs_by_key.values()
            )
        return value, 0

    masked: dict[str, Any] = {}
    total = 0
    for name, value in state.items():
        masked[name], count = walk(value)
        total += count
    return masked, total


def guard_texts(state: Mapping[str, Any]) -> dict[str, str]:
    """The state as the injection guard reads it: one text per source."""
    return {
        f"decision.{name}": value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
        for name, value in state.items()
    }


def answered(decision: DecisionActor, reply: DecisionReply) -> StageResult:
    """The step's result: each question's answer and probability as outputs, the answers in one line."""
    outputs: dict[str, Any] = {}
    said: list[str] = []
    for name in decision.questions:
        answer = reply.answers[name]
        outputs[name] = answer.value
        outputs[name + CONFIDENCE_SUFFIX] = answer.probability
        shown = ("yes" if answer.value else "no") if isinstance(answer.value, bool) else answer.value
        said.append(f"{name} = {shown} ({answer.probability:.2f})")
    return StageResult.model_validate(
        {"status": StageStatus.DONE, "summary": " · ".join(said)[:4000], "outputs": outputs}
    )


def decision_ref(decision: DecisionActor) -> str:
    """What the run's `playbook_checksum` records: the model and the digest of what is asked."""
    asked = json.dumps(
        {"state": decision.state, "questions": decision.model_dump(mode="json")["questions"]}, sort_keys=True
    )
    return f"decision:{decision.model}@sha256:{sha256(asked.encode()).hexdigest()[:12]}"[:80]


__all__ = [
    "DECISION_BACKEND",
    "DECISION_MARGIN_S",
    "DECISION_REFUSED",
    "DECISION_UNREACHABLE",
    "INVALID_DECISION",
    "answered",
    "assemble_state",
    "decide",
    "decision_request",
    "decision_run_id",
    "mask_state",
    "note_transition_taken",
]
