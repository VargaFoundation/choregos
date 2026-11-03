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
from typing import Any, Literal
from uuid import uuid4

from choregos_core import WorkflowEngine
from choregos_core.domain import TRACKER_FIELD
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Project, WorkflowDef, WorkItem
from ..errors import unprocessable
from .definitions import default_workflow, policy_engine, workflow_actif, workflow_model
from .initiator import initiator
from .llm_routing import Decision, Item, ask_the_model, book, routing_record


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
    #: Who filed it, as its tracker names them (#1179); a person in the console is their e-mail.
    reporter: str | None = None
    #: Who started it, `<kind>:<subject>` (`services/initiator.py`, ADR 0077 §2).
    initiated_by: str | None = None
    #: Le workflow demandé explicitement ; sinon le routage, sinon le défaut.
    workflow: str | None = None
    labels: tuple[str, ...] = ()
    #: Le type du ticket dans son tracker (Jira : `issuetype`), que le routage peut lire.
    item_type: str | None = None
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    #: Les champs du ticket, validés par `metadata.inputs` du workflow choisi.
    fields: dict[str, Any] = field(default_factory=dict)
    #: The budget given at launch (S27-06): only tightens the workflow's and the policy's.
    budget_usd: float | None = None
    #: An id derived by the caller (ADR 0008): an A2A message sent twice finds its work item (ADR 0078).
    item_id: str | None = None
    #: What the tracker knows about the ticket (S51-04), read from the tracker itself, never from a
    #: caller: stored as `fields["tracker"]`, beside the fields the workflow declares.
    tracker: dict[str, Any] | None = None


def _correspond(condition: dict[str, Any], etiquettes: set[str], type_: str | None) -> bool:
    """Une règle sans condition ne correspond à rien : elle ne capterait pas tout en silence."""
    une = set(condition.get("labels_any") or [])
    toutes = set(condition.get("labels_all") or [])
    attendu = condition.get("item_type")
    if not (une or toutes or attendu):
        return False
    return (not une or bool(une & etiquettes)) and toutes <= etiquettes and (not attendu or attendu == type_)


@dataclass(frozen=True, slots=True)
class Routed:
    """The workflow a birth goes to, and why: `explicit`, `rule`, `llm` or `default` (ADR 0116)."""

    row: WorkflowDef | None
    by: Literal["explicit", "rule", "llm", "default"]
    reason: str | None = None
    #: The model's decision, when the project's strategy asked it.
    decision: Decision | None = None


async def route(session: AsyncSession, project: Project, naissance: Naissance) -> Routed:
    """Explicit, then the rules (unless the strategy is `llm`), then the model (unless it is
    `labels`), then the default."""
    if naissance.workflow:
        explicit = await workflow_actif(session, project.id, naissance.workflow)
        if explicit is None:
            raise unprocessable(f"workflow `{naissance.workflow}` is unknown or inactive in this project")
        return Routed(explicit, "explicit")
    strategy = project.routing_strategy or "labels"
    labels = set(naissance.labels)
    if strategy != "llm":
        for index, rule in enumerate(project.workflow_routing or []):
            if _correspond(dict(rule.get("when") or {}), labels, naissance.item_type):
                target = await workflow_actif(session, project.id, str(rule.get("workflow")))
                if target is not None:
                    return Routed(target, "rule", f"routing rule {index + 1}")
    if strategy == "labels":
        return Routed(await default_workflow(session, project.id), "default")
    decision = await ask_the_model(
        session,
        project,
        Item(naissance.title, naissance.body, tuple(naissance.labels), naissance.item_type),
    )
    if decision.workflow is not None:
        target = await workflow_actif(session, project.id, decision.workflow)
        if target is not None:
            return Routed(target, "llm", decision.reason, decision)
    return Routed(await default_workflow(session, project.id), "default", decision.reason, decision)


async def choisir_workflow(
    session: AsyncSession, project: Project, naissance: Naissance
) -> WorkflowDef | None:
    """Explicite, puis la première règle de routage qui correspond, puis le défaut."""
    return (await route(session, project, naissance)).row


def refuse_reserved_fields(fields: dict[str, Any]) -> None:
    """A caller (a person, the MCP door, an agent's fan-out) may not write `fields.tracker` (S51-04):
    only what the tracker returned goes there, or an agent could be shown a forged requester."""
    if TRACKER_FIELD in fields:
        raise unprocessable(
            f"`fields.{TRACKER_FIELD}` is reserved: the platform writes what the tracker says about "
            "the ticket there",
            [{"loc": ["fields", TRACKER_FIELD], "msg": "reserved field"}],
        )


def with_tracker(fields: dict[str, Any], tracker: Any) -> dict[str, Any]:
    """The work item's fields with what the tracker says about it, replaced whole (S51-04)."""
    if not isinstance(tracker, dict):
        return dict(fields)
    return {**fields, TRACKER_FIELD: dict(tracker)}


def valider_les_champs(schema: dict[str, Any] | None, champs: dict[str, Any], workflow: str) -> None:
    """A field outside the schema, or a required field missing: 422, with its path.

    `fields.tracker` is the platform's (S51-04), not the workflow's: it is left out of the check, so a
    workflow whose inputs forbid extra properties still accepts a work item filed from Jira."""
    champs = {k: v for k, v in champs.items() if k != TRACKER_FIELD}
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


async def _budget_at_launch(
    session: AsyncSession, project: Project, workflow: Any, naissance: Naissance
) -> float | None:
    """The work item's own budget (S27-06, ADR 0047): the one given at launch, else the workflow's
    `budget.ticket_usd`. A budget given at launch may only tighten: wider than the workflow's or the
    policy's ticket budget for its size is refused (422), not silently clamped."""
    from_workflow = workflow.budget.ticket_usd if workflow.budget is not None else None
    if naissance.budget_usd is None:
        return from_workflow
    caps = {"policy": (await policy_engine(session, project.id)).budget_ticket(naissance.size)}
    if from_workflow is not None:
        caps[f"workflow `{workflow.metadata.name}`"] = from_workflow
    for source, cap in caps.items():
        if naissance.budget_usd > cap:
            raise unprocessable(
                f"budget_usd {naissance.budget_usd:.2f} $ is wider than the {source}'s ticket budget "
                f"({cap:.2f} $): a budget given at launch may only tighten it"
            )
    return naissance.budget_usd


async def verifier_la_naissance(session: AsyncSession, project: Project, naissance: Naissance) -> None:
    """Refuses (422) a birth that `nouveau_ticket` would refuse: unknown workflow, invalid fields,
    a budget wider than allowed. Nothing is written, so a caller can run it BEFORE a side effect
    it could not take back (the issue created in an external tracker, S39-16)."""
    refuse_reserved_fields(naissance.fields)
    ligne = await choisir_workflow(session, project, naissance)
    workflow = workflow_model(ligne)
    valider_les_champs(workflow.metadata.inputs, naissance.fields, workflow.metadata.name)
    await _budget_at_launch(session, project, workflow, naissance)


async def nouveau_ticket(session: AsyncSession, project: Project, naissance: Naissance) -> WorkItem:
    """Le seul constructeur : l'état initial DU workflow choisi, et l'épingle de sa version."""
    refuse_reserved_fields(naissance.fields)
    routed = await route(session, project, naissance)
    ligne = routed.row
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
        reporter=naissance.reporter,
        initiated_by=naissance.initiated_by,
        allowed_paths=list(naissance.allowed_paths),
        fields=with_tracker(naissance.fields, naissance.tracker),
        budget_usd=await _budget_at_launch(session, project, workflow, naissance),
    )
    if naissance.item_id is not None:
        item.id = naissance.item_id
    if naissance.labels:
        noter_les_etiquettes(item, naissance.labels)
    if routed.decision is not None:
        # How the model routed it stays on the work item (ADR 0116 §7); rules and explicit births
        # are told by the routing rules and the request themselves.
        item.documents = {
            **(item.documents or {}),
            "routing": routing_record(routed.by, routed.reason, routed.decision),
        }
    session.add(item)
    await session.flush()
    if routed.decision is not None:
        await book(session, project, routed.decision, item.id)
    return item


async def nouveau_ticket_d_essai(
    session: AsyncSession,
    project: Project,
    *,
    title: str,
    body: str | None,
    created_by: str | None,
    fields: dict[str, Any] | None = None,
) -> WorkItem:
    """The work item of an agent trial (S32-03): it never enters a workflow, so its state is `trial`.

    It lives here, with the only other `WorkItem(`, so that the single-constructor guard
    (test_un_seul_constructeur_de_ticket) still holds: a trial is not routed, it only needs a row
    the run, its cost and its log hang from. Its `fields` are the sample's (S51-04): a trial may play
    a ticket filed from a tracker, `fields.tracker` included, since it acts on nothing real.
    """
    item = WorkItem(
        project_id=project.id,
        tracker_key=f"TRIAL-{uuid4().hex[:8]}",
        title=title,
        body_snapshot=body,
        state="trial",
        fields=dict(fields or {}),
        created_by=created_by,
        initiated_by=initiator("trial", created_by),
    )
    session.add(item)
    await session.flush()
    return item
