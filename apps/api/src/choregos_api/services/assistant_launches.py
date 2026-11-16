# SPDX-License-Identifier: Apache-2.0
"""The launches of an assistant installation (ADR 0158, S66-22): bind, readiness, upgrade.

A launch is a workflow the assistant may PROPOSE to start. The manifest declares it (`launches`: a name, a
description, the `input_schema` the assistant fills, `required`) and names no project: the installation
binds it, by reference, to a project of the organisation and the name of one of its active workflows. The
work item a confirmed proposal creates is born under the project's active version of that workflow at that
moment (`conversation_launches.confirm`); nothing is copied here.

Binding needs `assistant:manage` on the installation (checked by the caller, `assistants.require_manager`)
AND `item:control` in the project: an owner cannot open, through an assistant, a project they could not file
work in. A launch "fits" its workflow when the workflow is active in the project and its `metadata.inputs`
accept what the launch fills: every input the workflow requires is one the launch requires, and, when the
workflow's inputs are closed, every property the launch declares is one they accept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import AssistantInstallation, AssistantLaunchBinding, Project
from ..errors import forbidden, not_found, unprocessable
from ..rbac import Permission, Principal
from ..schemas.assistants import AssistantLaunch, AssistantLaunchBind, AssistantReadinessItem
from .definitions import active_workflow_named, workflow_model

#: The kinds of a launch's readiness items (the contract's `AssistantReadinessItem.kind`).
UNBOUND = "launch_unbound"
WORKFLOW_MISSING = "launch_workflow_missing"
INPUTS_MISMATCH = "launch_inputs_mismatch"


@dataclass(frozen=True, slots=True)
class Misfit:
    kind: str
    message: str


def declared(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The launches a manifest declares, in its order."""
    return [entry for entry in (document or {}).get("launches") or [] if isinstance(entry, dict)]


def declaration(document: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    return next((entry for entry in declared(document) if entry.get("name") == name), None)


def is_required(launch: dict[str, Any]) -> bool:
    return bool(launch.get("required", True))


async def bindings(
    session: AsyncSession, installation_id: str
) -> dict[str, tuple[AssistantLaunchBinding, Project]]:
    """launch -> (its binding, the bound project)."""
    found = await session.execute(
        select(AssistantLaunchBinding, Project)
        .join(Project, Project.id == AssistantLaunchBinding.project_id)
        .where(AssistantLaunchBinding.installation_id == installation_id)
        .order_by(AssistantLaunchBinding.launch)
    )
    return {binding.launch: (binding, project) for binding, project in found.all()}


async def enabled_binding(
    session: AsyncSession, installation_id: str, launch: str
) -> tuple[AssistantLaunchBinding, Project] | None:
    found = (await bindings(session, installation_id)).get(launch)
    return found if found is not None and found[0].enabled else None


def _inputs_misfit(launch: dict[str, Any], inputs: dict[str, Any] | None, workflow: str) -> list[Misfit]:
    """Why the workflow's `metadata.inputs` cannot take what the launch fills (empty: they can)."""
    schema = launch.get("input_schema") or {}
    declared_properties = set((schema.get("properties") or {}).keys())
    required_by_launch = set(schema.get("required") or [])
    name = str(launch.get("name"))
    if inputs is None:
        if declared_properties:
            return [
                Misfit(
                    INPUTS_MISMATCH,
                    f"the workflow `{workflow}` declares no input, and the launch `{name}` fills "
                    + ", ".join(f"`{p}`" for p in sorted(declared_properties)),
                )
            ]
        return []
    problems: list[Misfit] = []
    for key in sorted(set(inputs.get("required") or []) - required_by_launch):
        problems.append(
            Misfit(
                INPUTS_MISMATCH,
                f"the workflow `{workflow}` requires the input `{key}`, which the launch `{name}` does not "
                "require",
            )
        )
    if inputs.get("additionalProperties") is False:
        accepted = set((inputs.get("properties") or {}).keys())
        for key in sorted(declared_properties - accepted):
            problems.append(
                Misfit(
                    INPUTS_MISMATCH,
                    f"the launch `{name}` fills `{key}`, which the workflow `{workflow}` does not accept",
                )
            )
    return problems


async def misfits(
    session: AsyncSession, project: Project, workflow: str, launch: dict[str, Any]
) -> list[Misfit]:
    """Why the project's workflow `workflow` cannot serve `launch` (empty: it can)."""
    row = await active_workflow_named(session, project.id, workflow)
    if row is None:
        return [
            Misfit(
                WORKFLOW_MISSING,
                f"the project `{project.slug}` has no active workflow `{workflow}` "
                f"(launch `{launch['name']}`)",
            )
        ]
    return _inputs_misfit(launch, workflow_model(row).metadata.inputs, workflow)


async def readiness_items(
    session: AsyncSession, row: AssistantInstallation, document: dict[str, Any], owners: list[str]
) -> list[AssistantReadinessItem]:
    """What the launches miss: a required launch unbound or switched off, a bound workflow that is gone or
    no longer takes the launch's inputs. Every item is the owners' to fix (ADR 0158 point 3)."""
    bound = await bindings(session, row.id)
    items: list[AssistantReadinessItem] = []

    def item(kind: str, name: str, message: str) -> AssistantReadinessItem:
        return AssistantReadinessItem(
            kind=kind,
            launch=name,
            message=message,
            fix_by="owners",
            groups=owners,
        )

    for launch in declared(document):
        name = str(launch["name"])
        found = bound.get(name)
        if found is None or not found[0].enabled:
            if not is_required(launch):
                continue
            if found is not None:
                problems = await misfits(session, found[1], found[0].workflow, launch)
                if problems:  # switched off by an upgrade the workflow no longer fits (S63-22's path)
                    items.extend(item(p.kind, name, f"{p.message}: bind it again") for p in problems)
                    continue
            state = "switched off" if found is not None else "not bound to a project's workflow"
            items.append(item(UNBOUND, name, f"the required launch `{name}` is {state}"))
            continue
        binding, project = found
        items.extend(
            item(p.kind, name, p.message) for p in await misfits(session, project, binding.workflow, launch)
        )
    return items


async def to_dtos(
    session: AsyncSession, row: AssistantInstallation, document: dict[str, Any] | None
) -> list[AssistantLaunch]:
    bound = await bindings(session, row.id)
    out: list[AssistantLaunch] = []
    for launch in declared(document):
        name = str(launch["name"])
        found = bound.get(name)
        out.append(
            AssistantLaunch(
                name=name,
                description=str(launch.get("description") or ""),
                required=is_required(launch),
                input_schema=dict(launch.get("input_schema") or {}),
                project=found[1].slug if found is not None else None,
                workflow=found[0].workflow if found is not None else None,
                enabled=found is not None and found[0].enabled,
                bound_by=found[0].bound_by if found is not None else None,
            )
        )
    return out


async def bind(
    session: AsyncSession,
    org_id: str,
    org: str,
    row: AssistantInstallation,
    document: dict[str, Any],
    name: str,
    body: AssistantLaunchBind,
    principal: Principal,
) -> None:
    """Binds the launch `name` to the project's workflow (the caller checked `assistant:manage`)."""
    launch = declaration(document, name)
    if launch is None:
        raise not_found("Launch", name)
    project = (
        await session.execute(select(Project).where(Project.org_id == org_id, Project.slug == body.project))
    ).scalar_one_or_none()
    # A project the caller cannot read is answered like one that does not exist.
    if project is None or not principal.can(Permission.PROJECT_READ, org, project.slug):
        raise unprocessable(f"`{body.project}` is not a project of {org}")
    if project.kind == "personal":
        raise unprocessable(f"`{body.project}` is a personal space: a launch is bound to a team's project")
    if not principal.can(Permission.ITEM_CONTROL, org, project.slug):
        raise forbidden(
            f"binding a launch to `{project.slug}` needs the right to create work items there "
            "(a developer of the project, or above)"
        )
    if not body.enabled and is_required(launch):
        raise unprocessable(f"`{name}` is a required launch: it cannot be switched off")
    problems = await misfits(session, project, body.workflow, launch)
    if problems:
        raise unprocessable(
            f"the workflow `{body.workflow}` of `{project.slug}` cannot serve the launch `{name}`",
            [{"loc": ["launch", name], "msg": p.message} for p in problems],
        )
    binding = (
        await session.execute(
            select(AssistantLaunchBinding).where(
                AssistantLaunchBinding.installation_id == row.id, AssistantLaunchBinding.launch == name
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        binding = AssistantLaunchBinding(org_id=org_id, installation_id=row.id, launch=name)
        session.add(binding)
    binding.project_id, binding.workflow = project.id, body.workflow
    binding.enabled, binding.bound_by = body.enabled, principal.email
    await session.flush()
    await record(session, principal, "assistant.launch.bind", org_id=org_id, target_type="assistant",
                 target_id=row.slug, launch=name, project=project.slug, workflow=body.workflow,
                 enabled=body.enabled)  # fmt: skip


async def upgrade(
    session: AsyncSession,
    row: AssistantInstallation,
    document: dict[str, Any],
    previous: dict[str, Any] | None,
) -> list[tuple[str, str, str]]:
    """Brings the launch bindings of `row` in line with the manifest its plugin now ships (S63-22's path):
    `(launch, kind, message)` for each change. A launch the manifest dropped has its binding switched off and
    kept; a bound launch whose declaration changed is checked again against its workflow and switched off when
    it no longer fits; a new launch stays unbound; a `required` flag that moved is reported."""
    launches = {str(launch["name"]): launch for launch in declared(document)}
    before = {str(launch["name"]): launch for launch in declared(previous)}
    changes: list[tuple[str, str, str]] = []
    bound = await bindings(session, row.id)
    for name, (binding, project) in bound.items():
        launch = launches.get(name)
        if launch is None:
            if binding.enabled:
                binding.enabled = False
                changes.append((name, "removed", f"`{name}` is no longer a launch: its binding is off"))
            continue
        if binding.enabled and launch != before.get(name):
            problems = await misfits(session, project, binding.workflow, launch)
            if problems:
                binding.enabled = False
                changes.append(
                    (
                        name,
                        "disabled",
                        f"`{name}` changed, the workflow `{binding.workflow}` of `{project.slug}` no longer "
                        "fits it: its binding is off: " + "; ".join(p.message for p in problems),
                    )
                )
        old = before.get(name)
        if old is not None and is_required(old) != is_required(launch):
            changes.append((name, "required_changed", f"`{name}` is now {_required_word(launch)}"))
    for name, launch in launches.items():
        if name in bound:
            continue
        if name not in before:
            changes.append(
                (name, "added", f"`{name}` is a new {_required_word(launch)} launch, not bound yet")
            )
        elif is_required(before[name]) != is_required(launch):
            changes.append((name, "required_changed", f"`{name}` is now {_required_word(launch)}"))
    await session.flush()
    return changes


def _required_word(launch: dict[str, Any]) -> str:
    return "required" if is_required(launch) else "optional"
