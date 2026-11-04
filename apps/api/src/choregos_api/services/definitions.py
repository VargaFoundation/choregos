# SPDX-License-Identifier: Apache-2.0
"""The active workflow and policy of a project, their models, and the defaults of a new project."""

from __future__ import annotations

from typing import Any

from choregos_contracts import Policy, ProjectConfig, Size, Workflow
from choregos_core import (
    PolicyEngine,
    checksum,
    load_preset,
    load_template,
    parse_policy,
    parse_workflow,
    policy_warnings,
)
from choregos_core.compat import alias_names
from choregos_core.dsl import dump_workflow
from choregos_core.policy import unenforced_governance
from choregos_core.policy_ceiling import PolicyCeiling, clamp
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    Organization,
    PolicyDef,
    Project,
    WorkflowDef,
    WorkItem,
)
from ..errors import conflict, former_code, unprocessable
from ..logging import get_logger

DEFAULT_WORKFLOW = "default-simple"
DEFAULT_POLICY_PRESET = "solo"


async def default_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    """The active version of the project's DEFAULT workflow (ADR 0031).

    Before, "the project's workflow" was the highest active version, whatever its name: with two
    active workflows, an offboarding work item would have run under onboarding. Without a named
    default (a project from before 0.14 not migrated), the former rule is kept.
    """
    project = await session.get(Project, project_id)
    query = select(WorkflowDef).where(WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True))
    if project is not None and project.default_workflow:
        query = query.where(WorkflowDef.name == project.default_workflow)
    return (await session.execute(query.order_by(WorkflowDef.version.desc()).limit(1))).scalar_one_or_none()


async def active_workflow(session: AsyncSession, project_id: str) -> WorkflowDef | None:
    """Deprecated: `default_workflow`, or `work_item_workflow` for a work item. Kept for one minor
    release, for the enterprise edition and the extensions that call it."""
    return await default_workflow(session, project_id)


async def work_item_workflow(session: AsyncSession, item: WorkItem) -> WorkflowDef | None:
    """The version the work item was born in, never another project's; failing that, the default."""
    if item.workflow_def_id:
        pinned = await session.get(WorkflowDef, item.workflow_def_id)
        if pinned is not None and pinned.project_id == item.project_id:
            return pinned
    return await default_workflow(session, item.project_id)


async def active_workflow_named(session: AsyncSession, project_id: str, name: str) -> WorkflowDef | None:
    """The active version of the project's workflow `name` (only one per name, ADR 0031)."""
    return (
        await session.execute(
            select(WorkflowDef).where(
                WorkflowDef.project_id == project_id,
                WorkflowDef.name == name,
                WorkflowDef.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


def _check_effects(workflow: Any) -> None:
    """An effect the platform does not know is refused AT PUBLICATION (S20-05): a typo must not wait
    for the arrival date of a work item to be discovered. Effects are a seam of the API
    (`declare_effect`), the core's validator does not see them."""
    from ..effects import declared_effects

    known = declared_effects()
    errors = []
    for index, transition in enumerate(workflow.transitions):
        if transition.action is None:
            continue
        for position, effect in enumerate(transition.action.effects):
            names = [effect.effect, *([str(effect.compensate.get("effect"))] if effect.compensate else [])]
            for name in names:
                if name not in known:
                    errors.append(
                        {
                            "loc": [f"/transitions/{index}/action/effects/{position}"],
                            "msg": f"unknown effect: {name} (known: {', '.join(sorted(known))})",
                            "code": "action.effect_unknown",
                        }
                    )
    if errors:
        raise unprocessable("invalid workflow", errors)


async def _check_named_agents(session: AsyncSession, project: Project, workflow: Workflow) -> None:
    """A workflow may not name a personal agent, nor another project's agent (ADR 0061): 422, and the
    message names the agent, its scope and the fix."""
    from .agent_catalogue import named_agents
    from .agent_governance import refusals_for_named_agents

    refusals = await refusals_for_named_agents(session, project.org_id, project.id, named_agents(workflow))
    if refusals:
        raise unprocessable(
            "the workflow names an agent that cannot run in this project: " + "; ".join(refusals),
            [{"loc": ["actors"], "msg": message} for message in refusals],
        )


async def _check_decision_steps(session: AsyncSession, project: Project, workflow: Workflow) -> None:
    """A decision step this project cannot run is refused when the workflow is saved (ADR 0121 §4), not
    at its first work item: `decision.gateway_unsupported` when the project's gateway serves no
    `/v1/systemone`, `decision.model_usage` when the organisation's catalogue does not enable the
    step's model for `decide`. The step checks both again when it runs: a catalogue changes."""
    from choregos_adapters.base import decision_route_refusal
    from choregos_contracts import DecisionActor

    from .gateway import project_gateway
    from .model_catalogue import load_policy

    asked = {t.by for t in workflow.transitions if t.by}
    decisions = {
        name: actor
        for name, actor in workflow.actors.items()
        if isinstance(actor, DecisionActor) and name in asked
    }
    if not decisions:
        return
    errors: list[dict[str, Any]] = []
    try:
        gateway: Any = await project_gateway(session, project)
    except Exception as error:  # a gateway the API cannot build is refused by the step, when it runs
        get_logger("choregos.definitions").warning(
            "gateway not built to check a decision", error=str(error)[:300]
        )
        gateway = None
    refusal = decision_route_refusal(gateway) if gateway is not None else None
    catalogue = await load_policy(session, project.org_id)
    for name, actor in decisions.items():
        if refusal is not None:
            errors.append(
                {"loc": [f"/actors/{name}"], "msg": refusal, "code": "decision.gateway_unsupported"}
            )
        if (unusable := catalogue.check_decision(actor.model)) is not None:
            errors.append({"loc": [f"/actors/{name}/model"], "msg": unusable, "code": "decision.model_usage"})
    if errors:
        raise unprocessable(
            "the workflow has a decision step this project cannot run: "
            + "; ".join(e["msg"] for e in errors),
            errors,
        )


async def _warn_about_memory_the_policy_disables(
    session: AsyncSession, project: Project, workflow: Workflow
) -> None:
    """A named agent that binds memory under a policy that disables it: logged, never refused (S51-01)."""
    from .memory_policy import log_memory_disabled_warnings, memory_disabled_warnings

    engine = await policy_engine(session, project.id)
    issues = await memory_disabled_warnings(
        session, project.org_id, [workflow], memory_enabled=engine.memory_enabled()
    )
    log_memory_disabled_warnings(issues, project.slug)


async def latest_version(session: AsyncSession, project_id: Any, name: str) -> int | None:
    """The highest version of a workflow of this name, active or not; `None` if it never existed."""
    return (
        await session.execute(
            select(func.max(WorkflowDef.version)).where(
                WorkflowDef.project_id == project_id, WorkflowDef.name == name
            )
        )
    ).scalar()


async def _check_budget(session: AsyncSession, project: Project, workflow: Workflow) -> None:
    """Refuses (422) a `budget.ticket_usd` wider than every ticket budget the policy gives (S27-12).

    A workflow's budget only tightens the policy's (S27-06, ADR 0047). The size of a work item is not
    known at publish, so the workflow is refused only when it is wider than the policy's largest
    size: no work item could ever be held to it. Between the sizes it is clamped at run time."""
    wanted = workflow.budget.ticket_usd if workflow.budget is not None else None
    if wanted is None:
        return
    engine = await policy_engine(session, project.id)
    cap = max(engine.budget_ticket(size) for size in Size)
    if wanted > cap:
        raise unprocessable(
            f"budget.ticket_usd {wanted:.2f} $ is wider than the policy's largest ticket budget "
            f"({cap:.2f} $): a workflow budget may only tighten the policy's",
            [{"loc": ["budget", "ticket_usd"], "msg": f"wider than the policy's {cap:.2f} $"}],
        )


async def publish_workflow(  # noqa: PLR0913 - the only write path: its options are named
    session: AsyncSession,
    principal: Any,
    project: Project,
    source_yaml: str,
    *,
    source: str = "platform",
    activate: bool = True,
    base_version: int | None = None,
    expected_name: str | None = None,
    becomes_default: bool = False,
    create_only: bool = False,
    origin: str | None = None,
) -> WorkflowDef:
    """The ONLY write path of a workflow (ADR 0031): the alias, the PUT by name, the restoration.

    Each publication adds a version, none is overwritten, and it deactivates only the active
    version of the SAME name: publishing `offboarding` does not touch `onboarding`. A pinned work
    item ends on its version.
    """
    workflow, report = parse_workflow(source_yaml, strict=False)
    if not report.valid:
        raise unprocessable(
            "invalid workflow",
            [
                {
                    "loc": [i.path or ""],
                    "msg": i.message,
                    "code": i.code,
                    "line": i.line,
                    "column": i.column,
                    **former_code(i.code),
                }
                for i in report.errors
            ],
        )
    name = workflow.metadata.name
    if expected_name is not None and name != expected_name:
        raise unprocessable(
            f"the YAML is named `{name}`, the route `{expected_name}`: `metadata.name` must match"
        )
    _check_effects(workflow)
    await _check_named_agents(session, project, workflow)
    await _check_budget(session, project, workflow)
    await _check_decision_steps(session, project, workflow)
    await _warn_about_memory_the_policy_disables(session, project, workflow)
    current = await active_workflow_named(session, project.id, name)
    if base_version is not None and (current is None or current.version != base_version):
        read_version = current.version if current is not None else "none"
        raise conflict(
            f"`{name}` has changed: you were editing version {base_version}, the active one is {read_version}"
        )
    already = await latest_version(session, project.id, name)
    # Creating overwrites nothing (#336): without this guard, naming a new workflow like an old one
    # published the text as the old one's next version, and the console's check did not close the
    # race between two creations in the same second.
    if create_only and already is not None:
        raise conflict(
            f"`{name}` already exists: open it to publish its next version, or choose another name"
        )
    version = (
        workflow.metadata.version if already is None or already < workflow.metadata.version else already + 1
    )
    if activate and current is not None:
        current.is_active = False
        await session.flush()
    row = WorkflowDef(
        project_id=project.id,
        name=name,
        version=version,
        source=source,
        origin=origin,
        yaml=source_yaml,
        json_doc=workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        checksum=checksum(workflow),
        is_active=activate,
        created_by=getattr(principal, "email", None),
    )
    if create_only:
        # Two creations of the same name in the same second both pass the guard above: the
        # (project, name, version) constraint tells them apart, and the second gets the same 409.
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError as failure:
            raise conflict(
                f"`{name}` already exists: open it to publish its next version, or choose another name"
            ) from failure
    else:
        session.add(row)
    if activate and (becomes_default or not project.default_workflow):
        project.default_workflow = name
    await session.flush()
    # A catalogue agent this workflow names, absent from the organisation, is installed (ADR 0040):
    # otherwise the work item would die at its first run (`AgentUnavailable`).
    from .agent_catalogue import install_named_agents

    await install_named_agents(session, project.org_id, [workflow], principal)
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


async def ensure_defaults(
    session: AsyncSession, project: Project, *, refuse_unenforced: bool = False
) -> tuple[WorkflowDef, PolicyDef]:
    """A new project receives what its template delivers (ADR 0031): its workflows, the default, the
    routing and the policy; without a template, `default-simple` and the `solo` preset (D14); its
    skills and agents enter the organisation when they are not there (S20-07); its extensions go to
    the extensions that install them (S20-09).

    Nothing is rewritten for a project that already has an active workflow or a policy.

    `refuse_unenforced` (project creation only) refuses a template whose policy promises guards
    Choregos does not enforce (ADR 0101). Lazy provisioning from read paths leaves it off, so a
    GET never answers 422.
    """
    from .templates import delivery, template_manifest

    workflow = await active_workflow(session, project.id)
    policy = await active_policy(session, project.id)
    delivered = None
    if workflow is None or policy is None:
        repo = (project.config or {}).get("repo") or {}
        delivered = delivery(
            *await template_manifest(session, project.template_ref, project.org_id),
            has_repository=bool(repo.get("url")),
        )
    if workflow is None:
        assert delivered is not None
        workflow = await _publish_what_template_delivers(session, project, delivered)
        if delivered.agents or delivered.skills or delivered.extensions or delivered.knowledge:
            from .installation import install_what_template_delivers

            await install_what_template_delivers(session, project, delivered, str(project.template_ref))
        # AFTER the template: its own agent of the same name wins over the catalogue's (ADR 0040).
        from ..rbac import SYSTEM
        from .agent_catalogue import install_named_agents

        await install_named_agents(
            session, project.org_id, [parse_workflow(s, strict=False)[0] for s in delivered.workflows], SYSTEM
        )
    if not project.default_workflow:
        project.default_workflow = workflow.name
    if policy is None:
        assert delivered is not None
        parsed_policy = parse_policy(delivered.policy)
        refused = unenforced_governance(parsed_policy) if refuse_unenforced else []
        if refused:
            raise unprocessable(
                f"template `{project.template_ref}` ships a policy with guards Choregos does not "
                "enforce (ADR 0101): " + "; ".join(i.message for i in refused),
                [
                    {"loc": ["policy", *(i.path or "").split(".")], "msg": i.message, "code": i.code}
                    for i in refused
                ],
            )
        report_unenforced_policy(parsed_policy, project.slug)
        policy = PolicyDef(
            project_id=project.id,
            name=parsed_policy.metadata.name,
            version=parsed_policy.metadata.version,
            yaml=delivered.policy,
            json_doc=parsed_policy.model_dump(mode="json", exclude_none=True),
            is_active=True,
        )
        session.add(policy)
    await session.flush()
    return workflow, policy


async def _publish_what_template_delivers(
    session: AsyncSession, project: Project, delivered: Any
) -> WorkflowDef:
    """The template's workflows, active together; returns the default's."""
    rows: dict[str, WorkflowDef] = {}
    for source in delivered.workflows:
        parsed, report = parse_workflow(source, strict=False)
        if not report.valid:
            raise unprocessable(
                f"template `{project.template_ref}` ships an invalid workflow",
                [
                    {"loc": [i.path or ""], "msg": i.message, "code": i.code, **former_code(i.code)}
                    for i in report.errors
                ],
            )
        name = parsed.metadata.name
        if name in rows:
            raise unprocessable(f"template `{project.template_ref}` ships workflow `{name}` twice")
        rows[name] = WorkflowDef(
            project_id=project.id,
            name=name,
            version=parsed.metadata.version,
            source="template",
            yaml=source,
            json_doc=parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum=checksum(parsed),
            is_active=True,
        )
    default = delivered.default or next(iter(rows))
    unknown = ({default} | {str(r.get("workflow")) for r in delivered.routing}) - set(rows)
    if unknown:
        template = project.template_ref
        raise unprocessable(f"template `{template}` names workflows it does not ship: {sorted(unknown)}")
    session.add_all(rows.values())
    if not project.default_workflow:
        project.default_workflow = default
    # The shape `PUT /workflow-routing` stores: a manifest does not go through another door.
    from ..schemas import RoutingRule

    project.workflow_routing = [
        RoutingRule.model_validate(r).model_dump(mode="json") for r in delivered.routing
    ]
    return rows[default]


def workflow_model(row: WorkflowDef | None) -> Workflow:
    if row is None:
        return load_template(DEFAULT_WORKFLOW)
    # A JSON document that does not say WHERE it starts cannot be trusted on the order of its
    # keys: the column is a `jsonb`, which sorts them by length then by bytes. The YAML is then
    # re-read, the only place where the declaration order survived. Documents written since the
    # field exists carry `initial` and take the fast path.
    if row.json_doc and row.json_doc.get("initial"):
        return Workflow.model_validate(row.json_doc)
    # A stored definition passed the rules of its day and must keep loading when a rule is added.
    parsed, _ = parse_workflow(row.yaml, strict=False)
    return parsed


def report_unenforced_policy(policy: Policy, project: str) -> list[str]:
    """Log, one line per key, what the policy writes and nobody enforces (ADR 0044).

    The `PolicyDto` contract has no warnings field: the log is where the platform says it, when a
    policy is saved and when a project is born. Returns the paths reported.
    """
    paths: list[str] = []
    # One logger per call: a module's, cached at first use, would not see a configuration set
    # after it (`structlog.testing.capture_logs`).
    logger = get_logger("choregos.policy")
    for issue in policy_warnings(policy):
        logger.warning(
            "policy.not_enforced",
            project=project,
            policy=policy.metadata.name,
            code=issue.code,
            path=issue.path,
            message=issue.message,
        )
        paths.append(issue.path or "")
    return paths


def policy_model(row: PolicyDef | None) -> Policy:
    if row is None:
        return load_preset(DEFAULT_POLICY_PRESET)
    if row.json_doc:
        return Policy.model_validate(row.json_doc)
    return parse_policy(row.yaml)


#: Where an organisation keeps its policy ceiling (S27-05), in `organizations.settings`.
CEILING_KEY = "policy_ceiling"


def ceiling_of(organisation: Organization | None) -> PolicyCeiling | None:
    """The organisation's policy ceiling, if it set one."""
    stored = ((organisation.settings or {}) if organisation is not None else {}).get(CEILING_KEY)
    if not stored or not stored.get("ceiling"):
        return None
    return PolicyCeiling.model_validate(stored["ceiling"])


async def effective_policy_and_clamps(session: AsyncSession, project_id: str) -> tuple[Policy, list[str]]:
    """The policy that runs: the project's, brought down to its organisation's ceiling — and what
    was brought down. A policy saved before the ceiling existed may be above it (S27-05)."""
    policy = policy_model(await active_policy(session, project_id))
    project = await session.get(Project, project_id)
    organisation = await session.get(Organization, project.org_id) if project is not None else None
    return clamp(policy, ceiling_of(organisation))


async def effective_policy(session: AsyncSession, project_id: str) -> Policy:
    return (await effective_policy_and_clamps(session, project_id))[0]


async def policy_engine(session: AsyncSession, project_id: str) -> PolicyEngine:
    return PolicyEngine(await effective_policy(session, project_id))


def project_config(project: Project) -> ProjectConfig:
    return ProjectConfig.model_validate(project.config)


def workflow_yaml(workflow: Workflow) -> str:
    return dump_workflow(workflow)


async def publier_workflow(  # lang: fr-ok alias:internal  # noqa: PLR0913
    session: AsyncSession,
    principal: Any,
    project: Project,
    source_yaml: str,
    *,
    source: str = "platform",
    activate: bool = True,
    base_version: int | None = None,
    nom_attendu: str | None = None,  # lang: fr-ok alias:internal
    devient_le_defaut: bool = False,  # lang: fr-ok alias:internal
    creation_seule: bool = False,  # lang: fr-ok alias:internal
    origin: str | None = None,
) -> WorkflowDef:
    """The former name of `publish_workflow`, with its former keywords: the routers, the local demo
    and the orchestrator's tests still call it so."""
    return await publish_workflow(
        session,
        principal,
        project,
        source_yaml,
        source=source,
        activate=activate,
        base_version=base_version,
        expected_name=nom_attendu,  # lang: fr-ok alias:internal
        becomes_default=devient_le_defaut,  # lang: fr-ok alias:internal
        create_only=creation_seule,  # lang: fr-ok alias:internal
        origin=origin,
    )


workflow_actif = active_workflow_named  # lang: fr-ok alias:internal
workflow_du_ticket = work_item_workflow  # lang: fr-ok alias:internal
signaler_ce_que_la_politique_n_applique_pas = report_unenforced_policy  # lang: fr-ok alias:internal
derniere_version = latest_version  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "workflow_actif": "active_workflow_named",  # lang: fr-ok alias:internal
        "workflow_du_ticket": "work_item_workflow",  # lang: fr-ok alias:internal
        "signaler_ce_que_la_politique_n_applique_pas": "report_unenforced_policy",  # lang: fr-ok
        "derniere_version": "latest_version",  # lang: fr-ok alias:internal
    },
)
