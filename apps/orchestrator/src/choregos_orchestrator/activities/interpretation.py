"""Les activités propres à l'interpréteur : charger le contexte d'un ticket, consigner la
mort d'un interpréteur, annoncer un ticket au train — et lire une erreur Temporal.

Elles vivaient dans le module du workflow, sous un `import activity` en fin de fichier ;
un workflow n'a pas à porter ses activités.
"""

from __future__ import annotations

from typing import Any

from choregos_api.services import persist_event
from choregos_contracts import EventType
from choregos_core import utcnow
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
        return {
            "workflow": bundle.workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
            "state": item.state,
            "ticket_budget_usd": bundle.engine.budget_ticket(item.size),
            "tracker_key": item.tracker_key,
            "project_slug": bundle.slug,
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
            "message": str(payload.get("message") or "interpréteur en échec"),
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


def message_de(exc: BaseException) -> str:
    """Le message utile d'une erreur Temporal : celui de la cause, pas « Activity task failed »."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ApplicationError) and cause.message:
            return cause.message
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    return str(cause or exc)[:500]


def activite_de(exc: BaseException) -> str | None:
    return exc.activity_type if isinstance(exc, ActivityError) else None


@activity.defn(name="signal_train")
async def signal_train(payload: dict[str, Any]) -> dict[str, Any]:
    """Annonce au train de l'environnement qu'un ticket est prêt à embarquer."""
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
                "labels": [],
                "pr_url": item.pr_url,
            },
        )
        return {"signalled": True}
