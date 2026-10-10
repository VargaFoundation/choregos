# SPDX-License-Identifier: Apache-2.0
"""Les activités propres à l'interpréteur : charger le contexte d'un ticket, consigner la
mort d'un interpréteur ou une migration, annoncer un ticket au train — et lire une erreur Temporal.

Elles vivaient dans le module du workflow, sous un `import activity` en fin de fichier ;
un workflow n'a pas à porter ses activités.
"""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Event, WorkflowDef
from choregos_api.services import persist_event, workflow_du_ticket, workflow_model
from choregos_contracts import EventType
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ActivityError, ApplicationError

from ..train_client import signal_release_train
from .base import db, load_work_item, project_bundle


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
            "ticket_budget_usd": bundle.engine.budget_ticket(item.size),
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
        return failure


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
