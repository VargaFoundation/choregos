# SPDX-License-Identifier: Apache-2.0
"""Les activités propres à l'interpréteur : charger le contexte d'un ticket, consigner la
mort d'un interpréteur ou une migration, annoncer un ticket au train — et lire une erreur Temporal.

Elles vivaient dans le module du workflow, sous un `import activity` en fin de fichier ;
un workflow n'a pas à porter ses activités.
"""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Event, Run, WorkflowDef
from choregos_api.services import persist_event, workflow_du_ticket, workflow_model
from choregos_api.services.budgets import ticket_budget, ticket_spent
from choregos_contracts import EventType
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ActivityError, ApplicationError

from ..train_client import signal_release_train
from .base import db, load_work_item, project_bundle
from .bilan import close_run_spend
from .execution import abandon_queued_runs


@activity.defn(name="load_context")
async def load_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Charge la définition épinglée, la politique et l'état courant du ticket."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        # Un interpréteur qui (re)démarre n'est plus mort : la marque tombe ici, et nulle
        # part ailleurs — c'est la seule activité que tout démarrage traverse.
        item.failure = None
        # La version où le ticket est né (ADR 0031). Un ticket d'avant l'épingle la reçoit ici, une
        # fois : rejouer l'activité relit la même. Seul le CORPS de l'activité change — aucune
        # commande nouvelle dans l'historique, et les historiques archivés rejouent.
        ligne = await workflow_du_ticket(session, item)
        if item.workflow_def_id is None and ligne is not None:
            item.workflow_def_id = ligne.id
        workflow = workflow_model(ligne)
        return {
            "workflow": workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
            "workflow_def_id": item.workflow_def_id,
            "state": item.state,
            "ticket_budget_usd": ticket_budget(bundle.engine, item),
            # What the ticket has cost so far, models and tools (S27-03): a restart keeps it.
            "ticket_cost_usd": await ticket_spent(session, item.id),
            # The policy's ceiling on a transition's attempts, by role (S27-08).
            "attempts_ceilings": bundle.engine.attempts_ceilings(),
            "tracker_key": item.tracker_key,
            "project_slug": bundle.slug,
            # Ce que lit une date d'action (`not_before`, S20-05) ; un changement arrive ensuite
            # par le signal `fields_changed`.
            "fields": dict(item.fields or {}),
        }


@activity.defn(name="record_workflow_failure")
async def record_workflow_failure(payload: dict[str, Any]) -> dict[str, Any]:
    """Écrit sur le ticket que son interpréteur est mort, et publie l'événement.

    C'est le workflow qui l'appelle en mourant. Ce n'est PAS un remplacement de l'état
    Temporal — l'API le lit aussi — mais c'est ce qui reste quand Temporal a purgé
    l'historique, et c'est ce que le front et la chronologie affichent.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        failure = {
            "message": str(payload.get("message") or "the interpreter failed"),
            "activity": payload.get("activity"),
            "state": payload.get("state") or item.state,
            "at": utcnow().isoformat(),
        }
        item.failure = failure
        await persist_event(
            session,
            EventType.WORKITEM_WORKFLOW_FAILED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            **failure,
        )
        open_runs = await _runs_without_spend(session, item.id)
    # The interpreter dies — `await_run` gave up, an activity failed for good: nothing will call
    # `collect_spend` for its run. Its spend is booked and its key revoked here (#523).
    # A run still waiting for a slot is removed at the executor first, or its suspended Job and
    # its Secret stay forever (#501); the key is revoked after, as `cancel_run` does.
    await abandon_queued_runs(payload["project_id"], open_runs)
    for run_id in open_runs:
        await close_run_spend(payload["project_id"], run_id)
    return failure


async def _runs_without_spend(session: Any, work_item_id: str) -> list[str]:
    """The ticket's runs whose spend was never collected — at most the last few."""
    rows = await session.execute(
        select(Run.id)
        .where(Run.work_item_id == work_item_id, Run.spend_collected.is_(False))
        .order_by(Run.created_at.desc())
        .limit(10)
    )
    return [str(run_id) for run_id in rows.scalars().all()]


@activity.defn(name="record_migration")
async def record_migration(payload: dict[str, Any]) -> dict[str, Any]:
    """Consigne une migration (`migrate`, ADR 0031) : acceptée, l'épingle du ticket suit la
    définition cible — un `continue_as_new` la rechargera ; refusée, le ticket reste sur la sienne,
    et l'événement le dit.

    Rejouable sans double effet (ADR 0008) : la clé de la migration (`<workflow>/<run>/<n>`) est
    dans l'événement, et une clé déjà consignée ne l'est pas deux fois.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        cle = str(payload["cle"])
        types = (str(EventType.WORKITEM_MIGRATED), str(EventType.WORKITEM_MIGRATION_REFUSED))
        consignes = (
            await session.execute(select(Event).where(Event.work_item_id == item.id, Event.type.in_(types)))
        ).scalars()
        if any((e.payload or {}).get("cle") == cle for e in consignes):
            return {"recorded": False}
        commun = {
            "project_id": bundle.project.id,
            "work_item_id": item.id,
            "project_slug": bundle.slug,
            "subject": item.tracker_key,
            "cle": cle,
            "from_state": payload.get("from_state"),
        }
        if payload.get("refus"):
            await persist_event(
                session, EventType.WORKITEM_MIGRATION_REFUSED, **commun, reason=str(payload["refus"])
            )
            return {"recorded": True, "pinned": False}
        cible = await session.get(WorkflowDef, str(payload.get("workflow_def_id") or ""))
        if cible is not None and cible.project_id == bundle.project.id:
            item.workflow_def_id = cible.id
        await persist_event(
            session,
            EventType.WORKITEM_MIGRATED,
            **commun,
            to_state=payload.get("to_state"),
            workflow_def_id=cible.id if cible is not None else None,
            workflow=payload.get("workflow"),
        )
        return {"recorded": True, "pinned": cible is not None}


@activity.defn(name="escalation_context")
async def escalation_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Where a parked work item resumes, for an interpreter that did not park it (S22-41).

    An interpreter started again from the database knows its state (`needs_human`) but not the
    stage that escalated: `rerun_stage` and an answer did nothing. Since S22-41 the escalation
    writes `parked` — `[parked state, origin state, transition]` — on its `state_changed` event;
    a work item that escalated before has it derived: the transition out of the state it came
    from, other than a person's, whose escalation leads where it now stands. Also returns how
    many attempts that transition already made: a run number is never reused, it names the run.

    Read only: replaying it changes nothing.
    """
    from choregos_core import WorkflowEngine

    async with db() as session:
        item = await load_work_item(session, payload["work_item_id"])
        state = str(payload.get("state") or item.state)
        events = (
            await session.execute(
                select(Event)
                .where(
                    Event.work_item_id == item.id,
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                )
                .order_by(Event.ts.desc())
            )
        ).scalars()
        arrival = next((e.payload or {} for e in events if (e.payload or {}).get("to") == state), None)
        if arrival is None:
            return {"parked": None}
        parked = arrival.get("parked")
        if not (isinstance(parked, list) and len(parked) == 3 and parked[0] == state):
            engine = WorkflowEngine(workflow_model(await workflow_du_ticket(session, item)))
            parked = await _derived_parking(session, item.id, engine, state, arrival)
        if parked is None:
            return {"parked": None}
        attempts = (
            await session.execute(
                select(Run.attempt).where(Run.work_item_id == item.id, Run.transition_id == parked[2])
            )
        ).scalars()
        return {"parked": [str(p) for p in parked], "attempts": max(attempts, default=0)}


async def _derived_parking(
    session: Any, work_item_id: str, engine: Any, state: str, arrival: dict[str, Any]
) -> list[str] | None:
    """The escalation context of a work item parked before S22-41, read from what it left."""
    origin = arrival.get("from")
    if not origin or origin == state or arrival.get("reason") == "external event":
        return None  # moved there by a person on the board: nothing escalated
    candidates = [
        t
        for t in engine.wf.transitions_from(origin)
        if engine.actor_kind(t) != "human" and state != t.to and state in _escalation_targets(engine, t)
    ]
    if not candidates:
        return None
    keys = {t.key for t in candidates}
    last_run = (
        await session.execute(
            select(Run.transition_id)
            .where(Run.work_item_id == work_item_id, Run.transition_id.in_(keys))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    key = last_run or candidates[0].key
    return [state, str(origin), str(key)]


def _escalation_targets(engine: Any, transition: Any) -> set[str]:
    """Every state a stage of `transition` may park the work item in (engine: escalation,
    question, budget, stage cap)."""
    budget, defaults = engine.wf.budget, engine.wf.defaults
    from_any = defaults.from_any_agent_state if defaults else None
    targets = {
        engine.question_state(),
        getattr(from_any, "on_budget_exceeded", None),
        budget.on_exceeded if budget is not None else None,
    }
    for retry in (transition.on_fail, transition.on_changes_requested):
        if retry is not None:
            targets.add(retry.escalate_to)
    return {str(t) for t in targets if t}


#: Ce que Temporal met à la place d'un message absent, littéralement
#: (`converter/_failure_converter.py`, branche `application_failure_info` :
#: `failure.message or "Application error"`). Le message n'est donc JAMAIS vide, et un test de
#: vacuité ne suffit pas : il faut reconnaître le bouche-trou pour préférer le type.
BOUCHE_TROU_TEMPORAL = frozenset({"Application error", "Timeout", "Activity task failed"})


def message_de(exc: BaseException) -> str:
    """Le message utile d'une erreur Temporal : celui de la cause, pas « Activity task failed ».

        Une exception ordinaire traverse Temporal quand même : le convertisseur en fait une
        `ApplicationError` dont `type` porte le nom de la classe et `message` le `str()`. Or beaucoup
        d'erreurs réseau se lèvent **sans message** — `httpx.ConnectTimeout()` n'en a aucun. Le repli
    valait alors `"Application error"` — la chaîne que Temporal substitue à un message
        absent — et le ticket ne disait plus rien de sa mort. C'est arrivé le 2026-09-27 sur le
        locataire dev : la vraie cause, l'API Kubernetes injoignable, ne vivait que dans le journal de
        l'orchestrateur, que personne ne lit avant d'avoir une raison de le lire. À défaut de message,
        le **type** est ce qu'il y a de plus utile.
    """
    repli: str | None = None
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ApplicationError):
            if cause.message and cause.message not in BOUCHE_TROU_TEMPORAL:
                return cause.message
            if cause.type and repli is None:
                repli = cause.type
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    return repli or str(cause or exc)[:500]


def activite_de(exc: BaseException) -> str | None:
    return exc.activity_type if isinstance(exc, ActivityError) else None


@activity.defn(name="signal_train")
async def signal_train(payload: dict[str, Any]) -> dict[str, Any]:
    """Annonce au train de l'environnement qu'un ticket est prêt à embarquer, avec ses étiquettes et
    l'approbation que son workflow exige du départ (ADR 0041) — absente des embarquements d'avant."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        await signal_release_train(
            bundle.slug,
            payload["env"],
            "merged",
            {
                "work_item_key": item.tracker_key,
                "title": item.title,
                "merged_at": utcnow().isoformat(),
                "sha": (item.documents or {}).get("merge_sha", ""),
                # Déclarée par l'agent dans `artifacts.reports["infra_pr"]` : le train
                # l'appliquera via Atlantis pendant le départ, après approbation.
                "infra_pr_url": (item.documents or {}).get("infra_pr_url"),
                "risk": item.risk,
                # Les étiquettes du ticket, gardées depuis sa naissance : `hotfix` ouvre la voie
                # express (#279). Un ticket né avant ce changement n'en a pas gardé.
                "labels": list((item.documents or {}).get("labels") or []),
                "pr_url": item.pr_url,
                **({"approval": payload["approval"]} if payload.get("approval") else {}),
            },
        )
        return {"signalled": True}
