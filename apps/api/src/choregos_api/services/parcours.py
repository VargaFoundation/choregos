# SPDX-License-Identifier: Apache-2.0
"""Le parcours d'un ticket dans son workflow (S22-01) : ce que la console anime.

La chronologie (`chronologie`) raconte l'histoire d'un ticket en phrases ; elle ne dit pas PAR QUELLE
arête du graphe il est passé, et la console devait la reconstruire en relisant ses titres. Ici, chaque
déplacement d'état en état est rattaché à la transition qui l'a porté — la transition elle-même, son
rejet, sa demande de changements, sa reprise après échec, son escalade, ou un défaut « depuis tout état
d'agent » —, en lisant la définition OÙ LE TICKET EST ÉPINGLÉ, pas celle du projet aujourd'hui.

Et chaque pas d'un acteur — un run d'agent, une demande à une personne, une action gouvernée, un
départ du train — est rendu avec sa transition et son rang : la carte du parcours sait ainsi quelle
case s'allume, combien de tours une revue a pris, et ce qui attend encore quelqu'un.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from choregos_contracts import EventType, Workflow
from choregos_contracts.workflow import AGENT_WILDCARD
from choregos_core import to_graph, to_process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Action, Event, HumanRequest, Release, Run, WorkItem
from ..schemas import JourneyMove, JourneyStep, WorkItemJourney
from .definitions import workflow_du_ticket, workflow_model


def arete_du_deplacement(wf: Workflow, de: str | None, vers: str, raison: str = "") -> tuple[str, str | None]:
    """`(kind, transition_id)` d'un déplacement `de → vers`.

    L'ordre départage les cas où deux arêtes mènent au même endroit : la transition elle-même
    d'abord (une transition explicite avant une transition joker), puis son rejet, sa demande de
    changements, sa reprise, son escalade ; puis les défauts du workflow. Un déplacement qu'aucune
    arête n'explique (un ticket déplacé à la main dans le tracker) reste `other` : on ne l'invente pas.
    """
    if de is None:
        return "start", None
    if raison.startswith("migrated to"):
        return "migrated", None
    return _par_une_transition(wf, de, vers) or _par_un_defaut(wf, de, vers) or ("other", None)


def _par_une_transition(wf: Workflow, de: str, vers: str) -> tuple[str, str | None] | None:
    candidates = wf.transitions_from(de)
    explicites = [t for t in candidates if t.from_ != AGENT_WILDCARD]
    jokers = [t for t in candidates if t.from_ == AGENT_WILDCARD]
    for t in [*explicites, *jokers]:
        if t.to == vers:
            return "nominal", t.key
    issues: list[tuple[str, Any]] = [
        ("reject", lambda t: t.on_reject == vers),
        (
            "changes_requested",
            lambda t: t.on_changes_requested is not None and t.on_changes_requested.to == vers,
        ),
        ("retry", lambda t: t.on_fail is not None and t.on_fail.to == vers),
        (
            "escalate",
            lambda t: any(
                r is not None and r.escalate_to == vers for r in (t.on_fail, t.on_changes_requested)
            ),
        ),
    ]
    for kind, mene_la in issues:
        for t in candidates:
            if mene_la(t):
                return kind, t.key
    return None


def _par_un_defaut(wf: Workflow, de: str, vers: str) -> tuple[str, str | None] | None:
    defauts = wf.defaults
    garages: set[str] = set()
    if defauts and defauts.from_any_agent_state:
        d = defauts.from_any_agent_state
        garages = {s for s in (d.on_question, d.on_budget_exceeded, d.on_timeout) if s}
        if vers in garages and de in wf.agent_driven_states():
            return "default", None
    if defauts and defauts.needs_human and defauts.needs_human.on_abandon == vers:
        return "default", None
    # Sorti d'un état où un humain était attendu, vers l'état d'où il venait : la réponse le relance.
    escalades = {
        reprise.escalate_to
        for t in wf.transitions
        for reprise in (t.on_fail, t.on_changes_requested)
        if reprise is not None
    }
    if de in garages | escalades:
        return "resume", None
    return None


def _texte(valeur: Any) -> str | None:
    return str(valeur) if valeur not in (None, "") else None


def _statut_de_la_demande(demande: HumanRequest) -> str:
    """`waiting` tant que personne n'a tranché ; sinon ce qui a été décidé, dit en un mot."""
    if demande.decided_at is None:
        return "waiting"
    decision = dict(demande.decision or {})
    if decision.get("kind") == "task":
        return "completed" if decision.get("approved") is not False else "rejected"
    if decision.get("kind") == "scope_change":
        return "scope_change"
    if decision.get("kind") == "question":
        return "answered"
    return "approved" if decision.get("approved") else "rejected"


def _rang(compteurs: dict[str, int], cle: str | None) -> int:
    """Le rang d'un pas sur sa transition : le premier, le deuxième… dans l'ordre du temps."""
    cle = cle or ""
    compteurs[cle] = compteurs.get(cle, 0) + 1
    return compteurs[cle]


def _moment(*valeurs: datetime | None) -> datetime | None:
    return next((v for v in valeurs if v is not None), None)


async def parcours(session: AsyncSession, item: WorkItem) -> WorkItemJourney:
    """La carte de la version où le ticket est épinglé, ses déplacements, et les pas de ses acteurs."""
    definition = await workflow_du_ticket(session, item)
    wf = workflow_model(definition)

    evenements = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.work_item_id == item.id,
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                )
                .order_by(Event.ts)
            )
        )
        .scalars()
        .all()
    )
    moves: list[JourneyMove] = []
    for evenement in evenements:
        de = _texte(evenement.payload.get("from"))
        vers = str(evenement.payload.get("to") or "")
        if not vers:
            continue
        raison = str(evenement.payload.get("reason") or "")
        kind, transition = arete_du_deplacement(wf, de, vers, raison)
        moves.append(
            JourneyMove.model_validate(
                {
                    "at": evenement.ts,
                    "from": de,
                    "to": vers,
                    "kind": kind,
                    "transition_id": transition,
                    "reason": raison or None,
                }
            )
        )

    steps: list[JourneyStep] = []
    runs = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    for run in runs:
        resultat = dict(run.result or {})
        sorties = dict(resultat.get("outputs") or {})
        preuves = {k: v for k, v in dict(resultat.get("evidence") or {}).items() if v is not None}
        steps.append(
            JourneyStep(
                id=run.id,
                kind="agent",
                transition_id=run.transition_id,
                actor=run.actor,
                role=run.stage_role,
                attempt=max(run.attempt or 1, 1),
                status=run.status,
                started_at=_moment(run.started_at, run.created_at),
                ended_at=run.ended_at,
                summary=_texte(resultat.get("summary")),
                verdict=_texte(sorties.get("verdict")),
                cost_usd=run.cost_usd or 0.0,
                model=run.model,
                evidence=preuves,
            )
        )

    acteurs_humains = {t.key: t.by for t in wf.transitions}
    demandes = (
        (
            await session.execute(
                select(HumanRequest)
                .where(HumanRequest.work_item_id == item.id)
                .order_by(HumanRequest.requested_at)
            )
        )
        .scalars()
        .all()
    )
    rangs: dict[str, int] = {}
    for demande in demandes:
        payload = dict(demande.payload or {})
        steps.append(
            JourneyStep(
                id=demande.id,
                kind="human",
                transition_id=demande.transition_id,
                actor=acteurs_humains.get(demande.transition_id or ""),
                role=demande.kind,
                attempt=_rang(rangs, demande.transition_id),
                status=_statut_de_la_demande(demande),
                started_at=demande.requested_at,
                ended_at=demande.decided_at,
                summary=_texte(payload.get("question") or payload.get("summary")),
                decided_by=demande.decided_by,
                due_at=demande.due_at,
            )
        )

    actions = (
        (
            await session.execute(
                select(Action).where(Action.work_item_id == item.id).order_by(Action.created_at)
            )
        )
        .scalars()
        .all()
    )
    rangs = {}
    for action in actions:
        transition = _texte(dict(action.proposed_by or {}).get("transition"))
        steps.append(
            JourneyStep(
                id=action.id,
                kind="action",
                transition_id=transition,
                actor="platform",
                role=action.kind,
                attempt=_rang(rangs, transition),
                status=action.status,
                started_at=action.created_at,
                ended_at=action.finished_at,
                summary=action.title,
            )
        )

    # Le train : une release qui emporte le ticket est le pas de la transition `via: release_train`
    # vers son environnement. Rangées par projet ; on ne garde que celles qui le nomment.
    trains = {t.train.env: t.key for t in wf.transitions if t.via == "release_train" and t.train}
    if trains:
        releases = (
            (
                await session.execute(
                    select(Release)
                    .where(Release.project_id == item.project_id, Release.created_at >= item.created_at)
                    .order_by(Release.created_at)
                )
            )
            .scalars()
            .all()
        )
        rangs = {}
        for release in releases:
            if release.env not in trains:
                continue
            emporte = any(
                e.get("work_item_id") == item.id or e.get("work_item_key") == item.tracker_key
                for e in release.items or []
                if isinstance(e, dict)
            )
            if not emporte:
                continue
            transition = trains[release.env]
            steps.append(
                JourneyStep(
                    id=release.id,
                    kind="action",
                    transition_id=transition,
                    actor="release_train",
                    role=f"release to {release.env}",
                    attempt=_rang(rangs, transition),
                    status=release.status,
                    started_at=_moment(release.started_at, release.created_at),
                    ended_at=release.ended_at,
                    summary=f"batch {release.batch_no} to {release.env}, "
                    f"{len(release.items or [])} work item(s)",
                    decided_by=release.approved_by,
                )
            )

    steps.sort(key=lambda s: (s.started_at is None, s.started_at or item.created_at))
    return WorkItemJourney(
        work_item_id=item.id,
        tracker_key=item.tracker_key,
        workflow_name=wf.metadata.name,
        workflow_version=wf.metadata.version,
        initial=wf.initial_state,
        state=item.state,
        closed=item.closed_at is not None,
        graph=to_graph(wf),
        process=to_process(wf),
        moves=moves,
        steps=steps,
    )
