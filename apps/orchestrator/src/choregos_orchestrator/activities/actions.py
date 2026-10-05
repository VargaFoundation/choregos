# SPDX-License-Identifier: Apache-2.0
"""Les activités de l'`ActionWorkflow` (ADR 0035) : charger l'action, jouer un effet sous sa clé,
le compenser, clore.

Le journal (`action_effects`) est la mémoire qui rend une reprise sûre :
- un effet est CONSIGNÉ (`started`) dans sa propre transaction AVANT d'être tenté ;
- il est CONFIRMÉ (`done`, avec sa réponse) après ;
- une activité rejouée qui trouve sa clé `done` rend la réponse consignée sans rien refaire ;
- une clé `started` non confirmée (un worker tué entre les deux) est retentée — les effets sont
  idempotents par construction (`declarer_un_effet`).
"""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Action, ActionEffect, Project
from choregos_api.effets import ContexteEffet, EffetRefuse, effet, rendre
from choregos_api.services import persist_event
from choregos_contracts import ActionStatus, EventType
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .base import db


async def _charger(session: Any, action_id: str) -> tuple[Action, Project]:
    action = await session.get(Action, action_id)
    if action is None:
        raise ApplicationError(
            f"action introuvable : {action_id}", type="ActionIntrouvable", non_retryable=True
        )
    projet = await session.get(Project, action.project_id)
    if projet is None:
        raise ApplicationError(f"projet introuvable : {action.project_id}", non_retryable=True)
    return action, projet


async def _faits(session: Any, action_id: str, avant: int) -> list[dict[str, Any]]:
    """Les réponses des effets déjà faits, dans l'ordre : ce que les suivants peuvent citer."""
    lignes = (
        await session.execute(
            select(ActionEffect)
            .where(ActionEffect.action_id == action_id, ActionEffect.position < avant)
            .order_by(ActionEffect.position)
        )
    ).scalars()
    return [dict(ligne.result or {}) for ligne in lignes]


@activity.defn
async def charger_l_action(action_id: str) -> dict[str, Any]:
    async with db() as session:
        action, _projet = await _charger(session, action_id)
        if action.status == ActionStatus.APPROVED.value:
            action.status = ActionStatus.RUNNING.value
        return {"effets": len(action.effects or []), "statut": action.status}


@activity.defn
async def executer_l_effet(entree: dict[str, Any]) -> dict[str, Any]:
    action_id, position = str(entree["action_id"]), int(entree["position"])
    cle = f"{action_id}:{position}"
    async with db() as session:
        action, projet = await _charger(session, action_id)
        ligne = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == cle))
        ).scalar_one_or_none()
        if ligne is not None and ligne.status == "done":
            # Déjà fait : la réponse consignée, sans rien refaire — c'est toute la garantie.
            return {"key": cle, "replayed": True, "result": ligne.result}
        spec = (action.effects or [])[position]
        contexte = {"params": action.params or {}, "effects": await _faits(session, action_id, position)}
        try:
            params = rendre(spec.get("with") or {}, contexte)
        except EffetRefuse as refus:
            raise ApplicationError(str(refus), type="EffetRefuse", non_retryable=True) from refus
        if ligne is None:
            ligne = ActionEffect(
                org_id=action.org_id,
                action_id=action_id,
                position=position,
                key=cle,
                effect=str(spec["effect"]),
                params=params,
                status="started",
                attempts=0,
            )
            session.add(ligne)
        ligne.attempts += 1
        ligne.params, ligne.status, ligne.error = params, "started", None
    # Consigné et validé AVANT la tentative : un worker tué ici laisse une clé `started`.
    try:
        async with db() as session:
            action, projet = await _charger(session, action_id)
            resultat = await effet(str(spec["effect"]))(ContexteEffet(session, action, projet), params)
    except EffetRefuse as refus:
        await _noter_l_echec(cle, str(refus))
        raise ApplicationError(str(refus), type="EffetRefuse", non_retryable=True) from refus
    except Exception as panne:
        await _noter_l_echec(cle, f"{type(panne).__name__}: {panne}")
        raise
    async with db() as session:
        ligne = (await session.execute(select(ActionEffect).where(ActionEffect.key == cle))).scalar_one()
        ligne.status, ligne.result, ligne.finished_at = "done", resultat, utcnow()
        action, projet = await _charger(session, action_id)
        await persist_event(
            session,
            EventType.ACTION_EFFECT_DONE,
            project_id=projet.id,
            work_item_id=action.work_item_id,
            project_slug=projet.slug,
            subject=action_id,
            key=cle,
            effect=ligne.effect,
        )
    return {"key": cle, "replayed": False, "result": resultat}


async def _noter_l_echec(cle: str, erreur: str) -> None:
    async with db() as session:
        ligne = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == cle))
        ).scalar_one_or_none()
        if ligne is not None:
            ligne.error = erreur[:2000]


@activity.defn
async def compenser_l_effet(entree: dict[str, Any]) -> dict[str, Any]:
    """Défait un effet fait, avec ce que l'action a déclaré pour lui. Ne lève jamais : ce qu'elle
    n'a pas pu défaire, elle le dit, et le workflow compense les suivants quand même."""
    action_id, position = str(entree["action_id"]), int(entree["position"])
    cle = f"{action_id}:{position}"
    async with db() as session:
        action, projet = await _charger(session, action_id)
        ligne = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == cle))
        ).scalar_one_or_none()
        if ligne is None or ligne.status != "done":
            return {"key": cle, "undone": False, "reason": "pas fait"}
        compensation = (action.effects or [])[position].get("compensate")
        if not compensation:
            ligne.status = "compensation_failed"
            ligne.error = "aucune compensation déclarée"
            return {"key": cle, "undone": False, "reason": "aucune compensation déclarée"}
        contexte = {
            "params": action.params or {},
            "effects": await _faits(session, action_id, position),
            "result": ligne.result or {},
        }
        try:
            params = rendre(compensation.get("with") or {}, contexte)
            await effet(str(compensation["effect"]))(ContexteEffet(session, action, projet), params)
        except Exception as panne:
            ligne.status, ligne.error = "compensation_failed", f"{type(panne).__name__}: {panne}"[:2000]
            return {"key": cle, "undone": False, "reason": ligne.error}
        ligne.status = "compensated"
        await persist_event(
            session,
            EventType.ACTION_EFFECT_COMPENSATED,
            project_id=projet.id,
            work_item_id=action.work_item_id,
            project_slug=projet.slug,
            subject=action_id,
            key=cle,
        )
        return {"key": cle, "undone": True}


@activity.defn
async def cloturer_l_action(entree: dict[str, Any]) -> dict[str, Any]:
    action_id, statut = str(entree["action_id"]), str(entree["status"])
    async with db() as session:
        action, projet = await _charger(session, action_id)
        action.status = statut
        action.error = entree.get("error")
        action.result = {"compensations": entree.get("compensations") or []}
        action.finished_at = utcnow()
        await persist_event(
            session,
            EventType.ACTION_SUCCEEDED if statut == ActionStatus.SUCCEEDED.value else EventType.ACTION_FAILED,
            project_id=projet.id,
            work_item_id=action.work_item_id,
            project_slug=projet.slug,
            subject=action_id,
            error=action.error,
        )
    return {"action_id": action_id, "status": statut}
