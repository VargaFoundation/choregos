"""Socle des activités : accès base, adaptateurs par projet, idempotence.

Toutes les activités sont **idempotentes** par `(run_id | work_item_id, step)` : Temporal
peut les rejouer sans effet double — c'est la règle qui protège du double coût.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from choregos_adapters import AdapterSet
from choregos_api.db.models import Connector, Organization, PolicyDef, Project, WorkflowDef, WorkItem
from choregos_api.db.session import session_scope
from choregos_api.services import policy_model, workflow_model
from choregos_contracts import Policy, ProjectConfig, Workflow
from choregos_core import PolicyEngine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ProjectBundle:
    """Tout ce qu'une activité doit connaître d'un projet, chargé en une fois."""

    project: Project
    org_slug: str
    config: ProjectConfig
    workflow: Workflow
    policy: Policy
    adapters: AdapterSet

    @property
    def engine(self) -> PolicyEngine:
        return PolicyEngine(self.policy)

    @property
    def slug(self) -> str:
        return self.project.slug


@asynccontextmanager
async def db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def load_project(session: AsyncSession, project_id: str) -> ProjectBundle:
    project = await session.get(Project, project_id)
    if project is None:
        rows = (await session.execute(select(Project).where(Project.slug == project_id))).scalars().all()
        project = rows[0] if rows else None
    if project is None:
        raise ValueError(f"projet inconnu : {project_id}")
    org = await session.get(Organization, project.org_id)
    workflow_row = (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == project.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    policy_row = (
        await session.execute(
            select(PolicyDef)
            .where(PolicyDef.project_id == project.id, PolicyDef.is_active.is_(True))
            .order_by(PolicyDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    connectors = (
        (await session.execute(select(Connector).where(Connector.project_id == project.id))).scalars().all()
    )
    return ProjectBundle(
        project=project,
        org_slug=org.slug if org else "",
        config=ProjectConfig.model_validate(project.config),
        workflow=workflow_model(workflow_row),
        policy=policy_model(policy_row),
        adapters=AdapterSet.from_connectors(
            {c.kind: {"type": c.type, "config": c.config} for c in connectors}
        ),
    )


def tracker_possede_les_tickets(adapter: object) -> bool:
    """Le connecteur tient-il les tickets ailleurs ? Un connecteur muet est supposé oui.

    Un adaptateur tiers, écrit avant que la question se pose, n'a pas cet attribut : le
    supposer externe est le choix sûr, puisque c'est le cas de tous les connecteurs livrés
    sauf `internal`.
    """
    return bool(getattr(adapter, "owns_items", True))


async def cle_de_ticket_interne(session: AsyncSession, bundle: ProjectBundle) -> str:
    """Attribue `<PRÉFIXE>-<n>` quand la plateforme tient elle-même les tickets.

    Sans cela, la clé rendue par le connecteur interne était le TITRE du ticket — illisible
    sur un board, et en collision avec la contrainte d'unicité `(project_id, tracker_key)`
    dès que deux findings se ressemblaient.

    La course entre deux créations simultanées est laissée à la contrainte d'unicité : elle
    fait échouer l'insertion, Temporal rejoue l'activité, et le numéro suivant est pris.
    Compter en base plutôt que tenir un compteur évite un deuxième endroit où l'état vit.
    """
    connecteur = (
        await session.execute(
            select(Connector).where(Connector.project_id == bundle.project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    configure = (connecteur.config or {}).get("key_prefix") if connecteur else None
    prefixe = str(configure or bundle.project.slug).upper()
    existantes = (
        (
            await session.execute(
                select(WorkItem.tracker_key).where(
                    WorkItem.project_id == bundle.project.id,
                    WorkItem.tracker_key.like(f"{prefixe}-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    numeros = [int(suffixe) for cle in existantes if (suffixe := cle.rsplit("-", 1)[-1]).isdigit()]
    return f"{prefixe}-{max(numeros, default=0) + 1}"


async def load_work_item(session: AsyncSession, work_item_id: str) -> WorkItem:
    item = await session.get(WorkItem, work_item_id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == work_item_id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise ValueError(f"ticket inconnu : {work_item_id}")
    return item


_ADAPTERS_OVERRIDE: AdapterSet | None = None


def set_adapters_override(adapters: AdapterSet | None) -> None:
    """Injection utilisée par les tests et par `make demo` : un seul jeu d'adaptateurs partagé."""
    global _ADAPTERS_OVERRIDE
    _ADAPTERS_OVERRIDE = adapters


def adapters_override() -> AdapterSet | None:
    return _ADAPTERS_OVERRIDE


async def project_bundle(session: AsyncSession, project_id: str) -> ProjectBundle:
    bundle = await load_project(session, project_id)
    override = adapters_override()
    if override is not None:
        bundle.adapters = override
    return bundle


def as_dict(value: Any) -> dict[str, Any]:
    """Sérialise un modèle pydantic ou un dict pour traverser une frontière d'activité."""
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json", by_alias=True))
    return dict(value or {})
