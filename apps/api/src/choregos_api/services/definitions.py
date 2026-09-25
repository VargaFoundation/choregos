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
)

DEFAULT_WORKFLOW = "default-simple"
DEFAULT_POLICY_PRESET = "solo"


async def active_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    return (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


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
    if row.json_doc:
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
