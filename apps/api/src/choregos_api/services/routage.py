# SPDX-License-Identifier: Apache-2.0
"""Où naît un ticket : le workflow qu'il suit, et le SEUL constructeur de ticket (ADR 0031).

Cinq chemins créaient un ticket — la console, la porte MCP, le webhook d'un tracker, le rattrapage,
le finding promu — et deux posaient l'état `inbox` à la main : sur un workflow qui commence ailleurs,
le ticket mourait à sa naissance. Ils passent tous par `nouveau_ticket`, qui choisit le workflow,
pose son état initial et épingle sa version ensemble. Un test refuse toute autre construction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from choregos_core import WorkflowEngine
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Project, WorkflowDef, WorkItem
from ..errors import unprocessable
from .definitions import default_workflow, workflow_actif, workflow_model


@dataclass(frozen=True, slots=True)
class Naissance:
    """Ce qui fait naître un ticket, d'où qu'il vienne."""

    tracker_key: str
    title: str
    body: str | None = None
    url: str | None = None
    size: str | None = None
    risk: str | None = None
    created_by: str | None = None
    #: Le workflow demandé explicitement ; sinon le routage, sinon le défaut.
    workflow: str | None = None
    labels: tuple[str, ...] = ()
    #: Le type du ticket dans son tracker (Jira : `issuetype`), que le routage peut lire.
    item_type: str | None = None
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    #: Les champs du ticket, validés par `metadata.inputs` du workflow choisi.
    fields: dict[str, Any] = field(default_factory=dict)


def _correspond(condition: dict[str, Any], etiquettes: set[str], type_: str | None) -> bool:
    """Une règle sans condition ne correspond à rien : elle ne capterait pas tout en silence."""
    une = set(condition.get("labels_any") or [])
    toutes = set(condition.get("labels_all") or [])
    attendu = condition.get("item_type")
    if not (une or toutes or attendu):
        return False
    return (not une or bool(une & etiquettes)) and toutes <= etiquettes and (not attendu or attendu == type_)


async def choisir_workflow(
    session: AsyncSession, project: Project, naissance: Naissance
) -> WorkflowDef | None:
    """Explicite, puis la première règle de routage qui correspond, puis le défaut."""
    if naissance.workflow:
        explicite = await workflow_actif(session, project.id, naissance.workflow)
        if explicite is None:
            raise unprocessable(f"workflow `{naissance.workflow}` is unknown or inactive in this project")
        return explicite
    etiquettes = set(naissance.labels)
    for regle in project.workflow_routing or []:
        if _correspond(dict(regle.get("when") or {}), etiquettes, naissance.item_type):
            cible = await workflow_actif(session, project.id, str(regle.get("workflow")))
            if cible is not None:
                return cible
    return await default_workflow(session, project.id)


def valider_les_champs(schema: dict[str, Any] | None, champs: dict[str, Any], workflow: str) -> None:
    """Un champ hors du schéma, ou un champ requis absent : 422, avec son chemin."""
    if schema is None:
        if champs:
            raise unprocessable(f"workflow `{workflow}` declares no field (`metadata.inputs`)")
        return
    import jsonschema

    erreurs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(champs), key=lambda e: list(e.path))
    if erreurs:
        raise unprocessable(
            f"fields refused by workflow `{workflow}`",
            [{"loc": ["fields", *e.path], "msg": e.message} for e in erreurs],
        )


def noter_les_etiquettes(item: WorkItem, etiquettes: Iterable[str]) -> None:
    """Range les étiquettes du ticket dans `documents.labels`, dans l'ordre, sans doublon.

    Elles servaient au routage puis se perdaient : `signal_train` embarquait avec `labels: []`, et un
    ticket étiqueté `hotfix` ne prenait jamais la voie express (#279). Elles vivent dans `documents`
    plutôt que dans une colonne : ni le contrat ni le schéma de la base ne changent. La liste est
    remplacée en entier, pas fusionnée : chaque tracker envoie l'état courant de ses étiquettes.
    """
    item.documents = {
        **(item.documents or {}),
        "labels": list(dict.fromkeys(str(e) for e in etiquettes if e)),
    }


async def nouveau_ticket(session: AsyncSession, project: Project, naissance: Naissance) -> WorkItem:
    """Le seul constructeur : l'état initial DU workflow choisi, et l'épingle de sa version."""
    ligne = await choisir_workflow(session, project, naissance)
    workflow = workflow_model(ligne)
    valider_les_champs(workflow.metadata.inputs, naissance.fields, workflow.metadata.name)
    item = WorkItem(  # le seul `WorkItem(` du code produit (test_un_seul_constructeur_de_ticket)
        project_id=project.id,
        tracker_key=naissance.tracker_key,
        title=naissance.title,
        body_snapshot=naissance.body,
        url=naissance.url,
        size=naissance.size,
        risk=naissance.risk,
        state=WorkflowEngine(workflow).initial_state,
        workflow_def_id=ligne.id if ligne is not None else None,
        created_by=naissance.created_by,
        allowed_paths=list(naissance.allowed_paths),
        fields=dict(naissance.fields),
    )
    if naissance.labels:
        noter_les_etiquettes(item, naissance.labels)
    session.add(item)
    await session.flush()
    return item
