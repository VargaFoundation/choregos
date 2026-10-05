# SPDX-License-Identifier: Apache-2.0
"""Le workflow et la politique actifs d'un projet, leurs modèles, et les défauts d'un projet neuf."""

from __future__ import annotations

from typing import Any

from choregos_contracts import Policy, ProjectConfig, Workflow
from choregos_core import (
    PolicyEngine,
    checksum,
    load_preset,
    load_template,
    parse_policy,
    parse_workflow,
)
from choregos_core.dsl import dump_workflow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    PolicyDef,
    Project,
    WorkflowDef,
    WorkItem,
)
from ..errors import conflict, unprocessable

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


async def workflow_actif(session: AsyncSession, project_id: str, nom: str) -> WorkflowDef | None:
    """La version active du workflow `nom` du projet (une seule par nom, ADR 0031)."""
    return (
        await session.execute(
            select(WorkflowDef).where(
                WorkflowDef.project_id == project_id, WorkflowDef.name == nom, WorkflowDef.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()


async def publier_workflow(  # noqa: PLR0913 - le seul chemin d'écriture : ses options sont nommées
    session: AsyncSession,
    principal: Any,
    project: Project,
    source_yaml: str,
    *,
    source: str = "platform",
    activate: bool = True,
    base_version: int | None = None,
    nom_attendu: str | None = None,
    devient_le_defaut: bool = False,
) -> WorkflowDef:
    """Le SEUL chemin d'écriture d'un workflow (ADR 0031) : l'alias, le PUT par nom, la restauration.

    Chaque publication ajoute une version — aucune n'est écrasée — et ne désactive que la version
    active du MÊME nom : publier `offboarding` ne touche pas à `onboarding`. Un ticket épinglé finit
    sur sa version.
    """
    workflow, report = parse_workflow(source_yaml, strict=False)
    if not report.valid:
        raise unprocessable(
            "workflow invalide",
            [
                {"loc": [i.path or ""], "msg": i.message, "code": i.code, "line": i.line, "column": i.column}
                for i in report.errors
            ],
        )
    nom = workflow.metadata.name
    if nom_attendu is not None and nom != nom_attendu:
        raise unprocessable(
            f"le YAML s'appelle `{nom}`, la route `{nom_attendu}` : `metadata.name` doit coïncider"
        )
    actuelle = await workflow_actif(session, project.id, nom)
    if base_version is not None and (actuelle is None or actuelle.version != base_version):
        lue = actuelle.version if actuelle is not None else "aucune"
        raise conflict(f"`{nom}` a changé : vous éditiez la version {base_version}, l'active est {lue}")
    deja = (
        await session.execute(
            select(func.max(WorkflowDef.version)).where(
                WorkflowDef.project_id == project.id, WorkflowDef.name == nom
            )
        )
    ).scalar()
    version = workflow.metadata.version if deja is None or deja < workflow.metadata.version else deja + 1
    if activate and actuelle is not None:
        actuelle.is_active = False
        await session.flush()
    row = WorkflowDef(
        project_id=project.id,
        name=nom,
        version=version,
        source=source,
        yaml=source_yaml,
        json_doc=workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        checksum=checksum(workflow),
        is_active=activate,
        created_by=getattr(principal, "email", None),
    )
    session.add(row)
    if activate and (devient_le_defaut or not project.default_workflow):
        project.default_workflow = nom
    await session.flush()
    await record(
        session,
        principal,
        "workflow.put",
        org_id=project.org_id,
        target_type="workflow",
        target_id=row.id,
        name=row.name,
        version=row.version,
    )
    return row


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
    """Un projet neuf reçoit ce que livre son gabarit (ADR 0031) : ses workflows, le défaut, le routage
    et la politique — sans gabarit, `default-simple` et le preset `solo` (D14).

    Rien n'est réécrit chez un projet qui a déjà un workflow actif ou une politique.
    """
    from .gabarits import livraison, manifeste_du_gabarit

    workflow = await active_workflow(session, project.id)
    policy = await active_policy(session, project.id)
    livree = None
    if workflow is None or policy is None:
        livree = livraison(*await manifeste_du_gabarit(session, project.template_ref))
    if workflow is None:
        assert livree is not None
        workflow = await _publier_ce_que_livre_le_gabarit(session, project, livree)
    if not project.default_workflow:
        project.default_workflow = workflow.name
    if policy is None:
        assert livree is not None
        parsed_policy = parse_policy(livree.politique)
        policy = PolicyDef(
            project_id=project.id,
            name=parsed_policy.metadata.name,
            version=parsed_policy.metadata.version,
            yaml=livree.politique,
            json_doc=parsed_policy.model_dump(mode="json", exclude_none=True),
            is_active=True,
        )
        session.add(policy)
    await session.flush()
    return workflow, policy


async def _publier_ce_que_livre_le_gabarit(
    session: AsyncSession, project: Project, livree: Any
) -> WorkflowDef:
    """Les workflows du gabarit, actifs ensemble ; rend celui du défaut."""
    lignes: dict[str, WorkflowDef] = {}
    for source in livree.workflows:
        parsed, report = parse_workflow(source, strict=False)
        if not report.valid:
            raise unprocessable(
                f"le gabarit `{project.template_ref}` livre un workflow invalide",
                [{"loc": [i.path or ""], "msg": i.message, "code": i.code} for i in report.errors],
            )
        nom = parsed.metadata.name
        if nom in lignes:
            raise unprocessable(f"le gabarit `{project.template_ref}` livre deux fois le workflow `{nom}`")
        lignes[nom] = WorkflowDef(
            project_id=project.id,
            name=nom,
            version=parsed.metadata.version,
            source="template",
            yaml=source,
            json_doc=parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum=checksum(parsed),
            is_active=True,
        )
    defaut = livree.defaut or next(iter(lignes))
    inconnus = ({defaut} | {str(r.get("workflow")) for r in livree.routage}) - set(lignes)
    if inconnus:
        gabarit = project.template_ref
        raise unprocessable(
            f"le gabarit `{gabarit}` désigne des workflows qu'il ne livre pas : {sorted(inconnus)}"
        )
    session.add_all(lignes.values())
    if not project.default_workflow:
        project.default_workflow = defaut
    # La forme que stocke `PUT /workflow-routing` : un manifeste ne passe pas par une autre porte.
    from ..schemas import RoutingRule

    project.workflow_routing = [RoutingRule.model_validate(r).model_dump(mode="json") for r in livree.routage]
    return lignes[defaut]


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
