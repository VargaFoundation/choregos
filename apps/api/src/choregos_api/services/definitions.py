# SPDX-License-Identifier: Apache-2.0
"""Le workflow et la politique actifs d'un projet, leurs modèles, et les défauts d'un projet neuf."""

from __future__ import annotations

from choregos_contracts import Policy, ProjectConfig, Workflow
from choregos_core import (
    PolicyEngine,
    checksum,
    load_preset,
    load_template,
    parse_policy,
    parse_workflow,
    template_yaml,
)
from choregos_core.dsl import dump_workflow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    PolicyDef,
    Project,
    WorkflowDef,
    WorkItem,
)

DEFAULT_WORKFLOW = "default-simple"
DEFAULT_POLICY_PRESET = "solo"


async def default_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    """La version active du workflow PAR DÉFAUT du projet (ADR 0031).

    Avant, « le workflow du projet » était la version active la plus haute, quel que soit son nom :
    avec deux workflows actifs, un ticket d'offboarding aurait tourné sous onboarding. Sans défaut
    nommé (un projet d'avant la 0.14 non migré), on garde l'ancienne règle.
    """
    project = await session.get(Project, project_id)
    requete = select(WorkflowDef).where(WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True))
    if project is not None and project.default_workflow:
        requete = requete.where(WorkflowDef.name == project.default_workflow)
    return (await session.execute(requete.order_by(WorkflowDef.version.desc()).limit(1))).scalar_one_or_none()


async def active_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    """Déprécié : `default_workflow`, ou `workflow_du_ticket` pour un ticket. Gardé une mineure, pour
    l'édition entreprise et les greffons qui l'appellent."""
    return await default_workflow(session, project_id)


async def workflow_du_ticket(session: AsyncSession, item: WorkItem) -> WorkflowDef | None:
    """La version où le ticket est né — jamais celle d'un autre projet ; à défaut, le défaut."""
    if item.workflow_def_id:
        epingle = await session.get(WorkflowDef, item.workflow_def_id)
        if epingle is not None and epingle.project_id == item.project_id:
            return epingle
    return await default_workflow(session, item.project_id)


async def active_policy(session: AsyncSession, project_id: str) -> PolicyDef | None:
    return (
        await session.execute(
            select(PolicyDef)
            .where(PolicyDef.project_id == project_id, PolicyDef.is_active.is_(True))
            .order_by(PolicyDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ensure_defaults(session: AsyncSession, project: Project) -> tuple[WorkflowDef, PolicyDef]:
    """Un projet neuf reçoit `default-simple` et le preset `solo` (D14)."""
    workflow = await active_workflow(session, project.id)
    if workflow is None:
        source = template_yaml(DEFAULT_WORKFLOW)
        parsed, _ = parse_workflow(source)
        workflow = WorkflowDef(
            project_id=project.id,
            name=parsed.metadata.name,
            version=parsed.metadata.version,
            source="template",
            yaml=source,
            json_doc=parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum=checksum(parsed),
            is_active=True,
        )
        session.add(workflow)
    if not project.default_workflow:
        project.default_workflow = workflow.name
    policy = await active_policy(session, project.id)
    if policy is None:
        from choregos_core import preset_yaml

        source = preset_yaml(DEFAULT_POLICY_PRESET)
        parsed_policy = parse_policy(source)
        policy = PolicyDef(
            project_id=project.id,
            name=parsed_policy.metadata.name,
            version=parsed_policy.metadata.version,
            yaml=source,
            json_doc=parsed_policy.model_dump(mode="json", exclude_none=True),
            is_active=True,
        )
        session.add(policy)
    await session.flush()
    return workflow, policy


def workflow_model(row: WorkflowDef | None) -> Workflow:
    if row is None:
        return load_template(DEFAULT_WORKFLOW)
    # Un document JSON qui ne dit pas OÙ il commence ne peut pas être cru sur l'ordre de ses
    # clés : la colonne est un `jsonb`, qui les range par longueur puis par octets. On relit alors
    # le YAML, seul endroit où l'ordre de déclaration a survécu. Les documents écrits depuis que
    # le champ existe portent `initial` et passent par le chemin rapide.
    if row.json_doc and row.json_doc.get("initial"):
        return Workflow.model_validate(row.json_doc)
    parsed, _ = parse_workflow(row.yaml)
    return parsed


def policy_model(row: PolicyDef | None) -> Policy:
    if row is None:
        return load_preset(DEFAULT_POLICY_PRESET)
    if row.json_doc:
        return Policy.model_validate(row.json_doc)
    return parse_policy(row.yaml)


async def policy_engine(session: AsyncSession, project_id: str) -> PolicyEngine:
    return PolicyEngine(policy_model(await active_policy(session, project_id)))


def project_config(project: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(project.config)


def workflow_yaml(workflow: Workflow) -> str:
    return dump_workflow(workflow)
