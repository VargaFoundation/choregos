# SPDX-License-Identifier: Apache-2.0
"""Les actions gouvernées (ADR 0035) : les proposer, les décider — jamais les exécuter ici.

Une action approuvée part dans Temporal (`ActionWorkflow`, `action-<id>`) : la réponse HTTP quitte
l'API avant le moindre effet. La décision se garde de quatre façons :
- une SESSION humaine, récente (`step_up_required` au-delà de la fraîcheur demandée) — un jeton
  d'API ou un client MCP ne décide jamais (ADR 0030) ;
- le RANG : au moins celui d'un approbateur demandé ;
- la SÉPARATION DES RÔLES : qui propose ne décide pas — ni l'humain qui l'a proposée, ni le
  propriétaire de l'agent qui l'a proposée ;
- la CONCURRENCE : la ligne est relue verrouillée, et le passage à `approved` est un
  compare-and-set ; de deux approbations simultanées, une seule démarre l'action.
"""

from __future__ import annotations

import uuid
from typing import Any

from choregos_contracts import ActionOrigin, ActionStatus, EventType
from choregos_core import utcnow
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Action, ActionEffect, Agent, Project, User, WorkItem
from ..errors import ApiError, conflict, forbidden, unprocessable
from ..fraicheur import age_de_l_authentification, exiger_une_authentification_fraiche
from ..schemas.actions import ActionCreate, ActionDto, ActionEffectDto, ApprovalSpec
from .evenements import persist_event

#: Du moins au plus : un approbateur doit avoir au moins le rang demandé.
RANGS = {"viewer": 0, "developer": 1, "release_captain": 2, "project_owner": 3, "org_admin": 4}


def rang(principal: Any, org: str, projet: str) -> int:
    roles = [principal.role_for(org, projet), principal.org_roles.get(org)]
    return max((RANGS.get(str(r), -1) for r in roles if r is not None), default=-1)


#: L'espace des identifiants d'actions de transition : un ticket, une transition, une tentative
#: donnent toujours le même — une activité rejouée retrouve l'action, elle n'en crée pas une autre.
_ESPACE_DES_TRANSITIONS = uuid.UUID("6f1c9a52-3b0e-4d7a-9e21-5c8b0d4f7a13")


async def politique_de_l_action(
    session: AsyncSession, projet: Project, effets: list[dict[str, Any]], params: dict[str, Any]
) -> tuple[str, list[str]]:
    """La plus stricte des politiques que ses effets — et leurs compensations — exigent pour CE
    projet (ADR 0034), et ce qui l'impose. `forbidden` refuse l'action ; `approval` demande une
    personne ; `allowed` : la politique de l'organisation suffit."""
    from ..effets import EffetRefuse, politique_de_l_effet, rendre
    from .connecteurs import RANG, politique_pour_le_projet

    pire, motifs = "allowed", []
    for spec in [*effets, *(dict(e["compensate"]) for e in effets if e.get("compensate"))]:
        nom = str(spec.get("effect"))
        if nom == "connector.call":
            try:
                appel = rendre(dict(spec.get("with") or {}), {"params": params, "effects": []})
                connecteur, operation = str(appel.get("connector", "")), str(appel.get("operation", ""))
            except EffetRefuse:
                # Le connecteur se déduit d'un effet précédent : on ne sait pas encore lequel, une
                # personne décide.
                connecteur, operation, politique = "?", "?", "approval"
            else:
                politique = await politique_pour_le_projet(session, projet, connecteur, operation)
            sujet = f"{connecteur}/{operation}"
        else:
            politique, sujet = politique_de_l_effet(nom), nom
        if politique != "allowed":
            motifs.append(f"{sujet} : {politique}")
        if RANG[politique] > RANG[pire]:
            pire = politique
    return pire, motifs


async def proposer(
    session: AsyncSession,
    projet: Project,
    corps: ActionCreate,
    *,
    origine: str,
    propose_par: dict[str, Any],
    principal: Any,
    run_id: str | None = None,
    action_id: str | None = None,
) -> Action:
    """Enregistre une action en attente de décision ; ses effets doivent être déclarés, et aucun
    ne doit appeler ce que ce projet s'interdit : une action condamnée ne se présente pas."""
    from ..effets import effets_declares

    connus = effets_declares()
    nommes = {e.effect for e in corps.effects} | {
        str(e.compensate.get("effect")) for e in corps.effects if e.compensate is not None
    }
    inconnus = sorted(nommes - connus)
    if inconnus:
        raise unprocessable(f"effets inconnus : {', '.join(inconnus)} (connus : {', '.join(sorted(connus))})")
    effets = [e.model_dump(by_alias=True, exclude_none=True) for e in corps.effects]
    pire, motifs = await politique_de_l_action(session, projet, effets, corps.params)
    if pire == "forbidden":
        interdits = [m for m in motifs if m.endswith("forbidden")]
        raise unprocessable(f"action interdite à ce projet : {'; '.join(interdits)}")
    action = Action(
        **({"id": action_id} if action_id else {}),
        org_id=projet.org_id,
        project_id=projet.id,
        work_item_id=corps.work_item_id,
        run_id=run_id,
        origin=origine,
        kind=corps.kind,
        title=corps.title,
        justification=corps.justification,
        params=corps.params,
        effects=effets,
        proposed_by=propose_par,
        approval=corps.approval.model_dump(mode="json"),
        decisions=[],
        status=ActionStatus.PENDING_APPROVAL.value,
    )
    session.add(action)
    await session.flush()
    await record(
        session,
        principal,
        "action.propose",
        org_id=projet.org_id,
        target_type="action",
        target_id=action.id,
        kind=corps.kind,
        origin=origine,
    )
    await persist_event(
        session,
        EventType.ACTION_PROPOSED,
        project_id=projet.id,
        work_item_id=corps.work_item_id,
        project_slug=projet.slug,
        subject=action.id,
        kind=corps.kind,
        title=corps.title,
        proposed_by=propose_par,
    )
    return action


async def proposer_pour_une_transition(
    session: AsyncSession,
    projet: Project,
    item: WorkItem,
    spec: dict[str, Any],
    *,
    transition: str,
    tentative: int,
    workflow_id: str,
) -> Action:
    """L'action qu'une transition SYSTÈME propose (S20-05), une fois par tentative.

    Son titre, sa justification et ses paramètres se rendent avec les champs du ticket. Quand
    chacune des opérations qu'elle appelle est permise à ce projet et que le workflow n'exige pas
    de validation, la politique décide : elle naît approuvée. Sinon elle attend une personne,
    comme toute autre — le workflow peut ajouter une validation, jamais en retirer une.
    Une variable absente, un effet inconnu, une opération interdite : `EffetRefuse` ou 422.
    """
    from choregos_contracts import TransitionAction

    from ..effets import rendre
    from ..rbac import SYSTEM

    identifiant = str(uuid.uuid5(_ESPACE_DES_TRANSITIONS, f"{item.id}:{transition}:{tentative}"))
    deja = await session.get(Action, identifiant)
    if deja is not None:
        return deja
    action = TransitionAction.model_validate(spec)
    contexte = {
        "fields": dict(item.fields or {}),
        "work_item": {"id": item.id, "key": item.tracker_key, "title": item.title},
    }
    corps = ActionCreate(
        kind=action.kind,
        title=str(rendre(action.title, contexte))[:300],
        justification=str(rendre(action.justification, contexte)) if action.justification else None,
        params=dict(rendre(action.params, contexte)),
        effects=[e.model_dump(by_alias=True, exclude_none=True) for e in action.effects],
        approval=ApprovalSpec.model_validate(action.approval.model_dump())
        if action.approval
        else ApprovalSpec(),
        work_item_id=item.id,
    )
    nouvelle = await proposer(
        session,
        projet,
        corps,
        origine=ActionOrigin.TRANSITION.value,
        propose_par={
            "kind": "system",
            "id": "system",
            "via": "transition",
            "transition": transition,
            "workflow_id": workflow_id,
        },
        principal=SYSTEM,
        action_id=identifiant,
    )
    pire, _motifs = await politique_de_l_action(session, projet, nouvelle.effects, nouvelle.params or {})
    if action.approval is None and pire == "allowed":
        from ..temporal import action_id as identifiant_temporal

        nouvelle.status = ActionStatus.APPROVED.value
        # Démarrée par l'activité qui l'a proposée, une fois la ligne écrite (orchestrateur).
        nouvelle.temporal_wf_id = identifiant_temporal(nouvelle.id)
        nouvelle.decisions = [
            {
                "by": "policy",
                "decision": "approve",
                "reason": "every operation it calls is allowed for this project",
                "at": utcnow().isoformat(),
                "auth_age_seconds": None,
            }
        ]
        await persist_event(
            session,
            EventType.ACTION_DECIDED,
            project_id=projet.id,
            work_item_id=item.id,
            project_slug=projet.slug,
            subject=nouvelle.id,
            decision="approve",
            by="policy",
            status=nouvelle.status,
        )
    return nouvelle


async def _prevenir_le_ticket(session: AsyncSession, projet: Project, action: Action) -> None:
    """Une action de transition réglée hors de son workflow — rejetée ici — réveille le ticket qui
    l'attend (`action_settled`) ; celle qui réussit ou échoue le fait depuis l'`ActionWorkflow`."""
    if action.origin != ActionOrigin.TRANSITION.value or action.work_item_id is None:
        return
    from ..temporal import get_temporal, interpreter_id

    cible = (action.proposed_by or {}).get("workflow_id")
    if not cible:
        item = await session.get(WorkItem, action.work_item_id)
        if item is None:
            return
        cible = interpreter_id(projet.slug, item.tracker_key)
    await get_temporal().signal(
        str(cible), "action_settled", {"action_id": action.id, "status": action.status}
    )


async def _proprietaire_de_l_agent(
    session: AsyncSession, propose_par: dict[str, Any], org_id: str
) -> str | None:
    if propose_par.get("kind") != "agent":
        return None
    slug = str(propose_par.get("id", "")).removeprefix("agent:")
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None or agent.owner_id is None:
        return None
    proprietaire = await session.get(User, agent.owner_id)
    return proprietaire.email.lower() if proprietaire is not None else None


async def decider(
    session: AsyncSession,
    projet: Project,
    org_slug: str,
    action_id: str,
    decision: str,
    raison: str | None,
    principal: Any,
) -> Action:
    if principal.kind != "user" or principal.authentifie_le is None:
        # Un jeton d'API, un client MCP : ils proposent, ils ne décident pas (ADR 0030).
        raise forbidden("une décision se prend dans une session humaine (decision_requires_session)")
    if decision == "reject" and not (raison or "").strip():
        raise unprocessable("un rejet dit pourquoi")
    action = (
        await session.execute(
            select(Action).where(Action.id == action_id, Action.project_id == projet.id).with_for_update()
        )
    ).scalar_one_or_none()
    if action is None:
        raise ApiError(404, "Action introuvable", action_id)
    if action.status != ActionStatus.PENDING_APPROVAL.value:
        raise conflict(f"l'action est déjà {action.status}")
    approbation = action.approval or {}
    approbateurs = approbation.get("approvers") or [{"role": "project_owner", "min": 1}]
    requis = min(RANGS.get(str(a.get("role")), 3) for a in approbateurs)
    if rang(principal, org_slug, projet.slug) < requis:
        raise forbidden("décider de cette action demande un rang d'approbateur")
    if approbation.get("separation_of_duties", True):
        propose_par = action.proposed_by or {}
        moi = principal.email.lower()
        if (propose_par.get("kind") == "user" and str(propose_par.get("id", "")).lower() == moi) or (
            await _proprietaire_de_l_agent(session, propose_par, projet.org_id)
        ) == moi:
            raise unprocessable(
                "séparation des rôles : qui propose (ou possède l'agent qui propose) ne décide pas"
            )
    if any(d.get("by", "").lower() == principal.email.lower() for d in action.decisions or []):
        raise conflict("vous avez déjà décidé de cette action")
    age = (
        exiger_une_authentification_fraiche(principal, int(approbation.get("step_up_minutes", 10)))
        if decision == "approve"
        else age_de_l_authentification(principal)
    )
    entree = {
        "by": principal.email,
        "decision": decision,
        "reason": raison,
        "at": utcnow().isoformat(),
        "auth_age_seconds": age,
    }
    decisions = [*(action.decisions or []), entree]
    approuvees = {d["by"].lower() for d in decisions if d.get("decision") == "approve"}
    minimum = max(int(a.get("min", 1)) for a in approbateurs)
    statut = (
        ActionStatus.REJECTED.value
        if decision == "reject"
        else ActionStatus.APPROVED.value
        if len(approuvees) >= minimum
        else ActionStatus.PENDING_APPROVAL.value
    )
    # Compare-and-set : la ligne n'avance que si elle est encore en attente. Sur PostgreSQL le
    # verrou de la lecture sérialise déjà ; ailleurs, c'est ce qui garde le second décideur.
    fait = await session.execute(
        update(Action)
        .where(Action.id == action.id, Action.status == ActionStatus.PENDING_APPROVAL.value)
        .values(decisions=decisions, status=statut, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if fait.rowcount != 1:  # type: ignore[attr-defined]
        raise conflict("une autre décision vient d'être prise")
    await session.refresh(action)
    await record(
        session,
        principal,
        "action.decide",
        org_id=projet.org_id,
        target_type="action",
        target_id=action.id,
        decision=decision,
        status=statut,
        auth_age_seconds=age,
    )
    await persist_event(
        session,
        EventType.ACTION_DECIDED,
        project_id=projet.id,
        work_item_id=action.work_item_id,
        project_slug=projet.slug,
        subject=action.id,
        decision=decision,
        by=principal.email,
        status=statut,
    )
    if statut == ActionStatus.APPROVED.value:
        from ..temporal import action_id as identifiant
        from ..temporal import get_temporal

        action.temporal_wf_id = await get_temporal().start_action(
            identifiant(action.id), {"action_id": action.id}
        )
    elif statut == ActionStatus.REJECTED.value:
        await _prevenir_le_ticket(session, projet, action)
    return action


async def dto(session: AsyncSession, action: Action) -> ActionDto:
    journal = (
        await session.execute(
            select(ActionEffect).where(ActionEffect.action_id == action.id).order_by(ActionEffect.position)
        )
    ).scalars()
    resultat = ActionDto.model_validate(action)
    resultat.journal = [ActionEffectDto.model_validate(e) for e in journal]
    projet = await session.get(Project, action.project_id)
    resultat.project_slug = projet.slug if projet is not None else None
    return resultat
