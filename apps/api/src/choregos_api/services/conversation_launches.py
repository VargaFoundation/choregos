# SPDX-License-Identifier: Apache-2.0
"""Launches in an assistant's conversation (ADR 0158, S66-22): propose, confirm, follow.

An installation whose launches are bound offers its conversations one tool per bound, enabled launch,
`launch__<name>(title, inputs)`, and `launch_status(action_id?)`. Calling a launch tool starts NOTHING: it
stores a `ConversationAction` of kind `launch`, `proposed`, with the arguments, a summary and its `target`
(the project and the workflow bound at that moment). It is the pending action of S63-10: the conversation's
owner confirms it in the console or the embedded component, the door lists it with its `confirm_url` and
never executes it.

Confirming (`confirm`) re-reads the binding (still enabled, still the proposal's project and workflow, else
409 `launch_rebound`) and THE CONFIRMING PERSON'S rights from their own request: without `item:control` in
the project, 403 `launch_forbidden` and nothing is created. The work item is then filed through the one
creation path (`tickets.create_work_item`), the person as principal: reporter, initiator, the project's
tracker, its budget and its journal. `launch_status` reads, with the person's current rights, what each
launch of the conversation became: the work item's state and its artefacts (ADR 0129), never their content.

Every call of both tools writes a line of the reads journal (ADR 0133 point 6): the launch as the need,
`propose` or `status` as the operation, the work item's key once there is one.
"""

from __future__ import annotations

import json
from typing import Any

from choregos_contracts import Role
from choregos_core import utcnow
from choregos_core.prompt_loop import LoopTool
from fastapi import status as http
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..config import get_settings
from ..db.models import (
    AgentSessionRecord,
    AssistantInstallation,
    AssistantRead,
    ConversationAction,
    Membership,
    Organization,
    Project,
    User,
    WorkItem,
)
from ..errors import ApiError, conflict
from ..rbac import Permission, Principal
from ..schemas.tickets import WorkItemCreate
from . import assistant_launches, assistants
from .agent_sessions import journal

#: The tools of a launch are `launch__<name>`; a need may not be called `launch` (ADR 0158 point 1).
PREFIX = "launch__"
STATUS_TOOL = "launch_status"
#: What the action stores as its operation.
START = "start"
TITLE_MAX = 500


def _console(path: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}{path}"


async def _offered_launches(
    session: AsyncSession, record: AgentSessionRecord
) -> tuple[list[dict[str, Any]], bool]:
    """The declarations of the bound, enabled launches; and whether any launch is bound at all."""
    if record.kind != "conversation" or not record.installation_id or not record.user_id:
        return [], False
    row = await session.get(AssistantInstallation, record.installation_id)
    if row is None:
        return [], False
    bound = await assistant_launches.bindings(session, row.id)
    if not bound:
        return [], False
    document = await assistants.manifest(session, row)
    offered = [
        launch
        for launch in assistant_launches.declared(document)
        if (found := bound.get(str(launch["name"]))) is not None and found[0].enabled
    ]
    return offered, True


async def offered(session: AsyncSession, record: AgentSessionRecord) -> dict[str, LoopTool]:
    """The turn's launch tools: one per bound, enabled launch, and `launch_status` once any is bound."""
    launches, any_bound = await _offered_launches(session, record)
    tools: dict[str, LoopTool] = {}
    for launch in launches:
        name = f"{PREFIX}{launch['name']}"
        description = (
            f"{launch.get('description') or launch['name']} - calling it does NOT start anything: it "
            "PROPOSES to start this workflow; the person confirms or refuses it in the console, and the work "
            "is then filed in their name. Give a short title and the inputs."
        )
        parameters = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": TITLE_MAX},
                "inputs": dict(launch.get("input_schema") or {"type": "object"}),
            },
            "required": ["title", "inputs"],
            "additionalProperties": False,
        }
        tools[name] = LoopTool(name, description, parameters)
    if any_bound:
        tools[STATUS_TOOL] = LoopTool(
            STATUS_TOOL,
            "What became of the workflows started from this conversation: for each, the work item's key, "
            "title and state, whether it ended, and its artefacts with the address where the person reads "
            "them. `action_id` narrows it to one proposal.",
            {
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "additionalProperties": False,
            },
        )
    return tools


async def person_of(session: AsyncSession, record: AgentSessionRecord) -> Principal | None:
    """The conversation's owner with their roles in the organisation and its projects, as a request of theirs
    would carry them; `None` once they are no longer a member."""
    user = await session.get(User, record.user_id) if record.user_id else None
    organisation = await session.get(Organization, record.org_id)
    if user is None or organisation is None:
        return None
    rows = await session.execute(
        select(Membership.role, Project.slug)
        .outerjoin(Project, Project.id == Membership.project_id)
        .where(Membership.user_id == user.id, Membership.org_id == organisation.id)
    )
    org_roles: dict[str, Role] = {}
    project_roles: dict[str, Role] = {}
    for role, project_slug in rows.all():
        if project_slug:
            project_roles[f"{organisation.slug}/{project_slug}"] = Role(role)
        else:
            org_roles[organisation.slug] = Role(role)
    if not org_roles and not project_roles:
        return None
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name or "",
        org_roles=org_roles,
        project_roles=project_roles,
        groups=[str(g) for g in (user.groups or [])],
    )


def _journal_line(
    record: AgentSessionRecord, launch: str, operation: str, key: str | None = None
) -> AssistantRead:
    return AssistantRead(
        org_id=record.org_id,
        installation_id=record.installation_id or "",
        user_id=record.user_id or "",
        conversation_id=record.id,
        work_item_key=key,
        need=launch,
        operation=operation,
        at=utcnow(),
    )


def summary_of(launch: str, project: str, workflow: str, title: str) -> str:
    text = f"start `{workflow}` in `{project}` ({launch}): {title}"
    return text if len(text) <= 300 else text[:299] + "..."


async def call(
    session: AsyncSession, record: AgentSessionRecord, name: str, arguments: dict[str, Any]
) -> str:
    """The agent called a launch tool or `launch_status`; what it reads back."""
    if name == STATUS_TOOL:
        return await _status(session, record, arguments.get("action_id"))
    return await _propose(session, record, name.removeprefix(PREFIX), arguments)


async def _propose(
    session: AsyncSession, record: AgentSessionRecord, launch_name: str, arguments: dict[str, Any]
) -> str:
    row = await session.get(AssistantInstallation, record.installation_id) if record.installation_id else None
    found = (
        await assistant_launches.enabled_binding(session, row.id, launch_name) if row is not None else None
    )
    document = await assistants.manifest(session, row) if row is not None else None
    launch = assistant_launches.declaration(document, launch_name)
    if row is None or found is None or launch is None:
        return f"not proposed: the launch `{launch_name}` is not bound in this installation"
    binding, project = found
    session.add(_journal_line(record, launch_name, "propose"))
    title = arguments.get("title")
    inputs = arguments.get("inputs")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        return f"not proposed: give a `title` of 1 to {TITLE_MAX} characters"
    if not isinstance(inputs, dict):
        return "not proposed: `inputs` must be an object"
    errors = sorted(
        Draft202012Validator(launch.get("input_schema") or {"type": "object"}).iter_errors(inputs),
        key=lambda e: list(e.path),
    )
    if errors:
        said = "; ".join(f"{'/'.join(map(str, e.path)) or 'inputs'}: {e.message}" for e in errors[:5])
        return f"not proposed: the inputs do not match the launch's schema: {said}"
    organisation = await session.get(Organization, record.org_id)
    person = await person_of(session, record)
    if (
        person is None
        or organisation is None
        or not person.can(Permission.ITEM_CONTROL, organisation.slug, project.slug)
    ):
        return (
            f"not proposed: the person may not start work in the project `{project.slug}` (it needs the "
            "developer role there, or above). Tell them so; nothing was proposed."
        )
    action = ConversationAction(
        org_id=record.org_id,
        conversation_id=record.id,
        user_id=record.user_id or "",
        need=launch_name,
        operation=START,
        kind="launch",
        arguments={"title": title.strip(), "inputs": inputs},
        summary=summary_of(launch_name, project.slug, binding.workflow, title.strip()),
        target={"project_id": project.id, "project": project.slug, "workflow": binding.workflow},
    )
    session.add(action)
    await session.flush()
    await journal(
        session,
        record,
        "action.proposed",
        {"action_id": action.id, "need": launch_name, "operation": START, "summary": action.summary},
    )
    return (
        f"Action `{action.id}` is proposed and awaits the person's confirmation: {action.summary}. NOTHING "
        "was started. Tell the person what you propose and that they confirm or refuse it in the console; "
        "do not call the tool again for the same request."
    )


async def confirm(
    session: AsyncSession, record: AgentSessionRecord, action: ConversationAction, principal: Principal
) -> ConversationAction:
    """Files the work item as `principal`, the conversation's owner confirming in their own request. The
    caller checked the owner, the audience and the action's state."""
    from .tickets import create_work_item

    target = action.target or {}
    found = (
        await assistant_launches.enabled_binding(session, record.installation_id, action.need)
        if record.installation_id
        else None
    )
    if found is None:
        raise conflict(
            f"the launch `{action.need}` is no longer bound in this installation: nothing is started",
            extra={"code": "not_available"},
        )
    binding, project = found
    if (binding.project_id, binding.workflow) != (target.get("project_id"), target.get("workflow")):
        raise conflict(
            f"the launch `{action.need}` is now bound to `{binding.workflow}` of `{project.slug}`, not to "
            "what was proposed: nothing is started, the assistant must propose it again",
            extra={"code": "launch_rebound"},
        )
    organisation = await session.get(Organization, record.org_id)
    org_slug = organisation.slug if organisation is not None else ""
    if not principal.can(Permission.ITEM_CONTROL, org_slug, project.slug):
        raise ApiError(
            http.HTTP_403_FORBIDDEN,
            "Forbidden",
            f"you may not create work items in `{project.slug}` (it needs the developer role there, or "
            "above): nothing is started",
            extra={"code": "launch_forbidden"},
        )
    arguments = dict(action.arguments)
    inputs = json.dumps(arguments.get("inputs") or {}, indent=2, ensure_ascii=False)
    body = (
        f"Started from the assistant `{await _installation_slug(session, record)}`, conversation "
        f"{record.id}, confirmed by {principal.email}.\n\n```json\n{inputs}\n```"
    )
    item = await create_work_item(
        session,
        principal,
        project,
        WorkItemCreate(
            title=str(arguments.get("title") or action.need),
            body=body,
            workflow=binding.workflow,
            fields=dict(arguments.get("inputs") or {}),
        ),
        canal="assistant",
    )
    now = utcnow()
    action.confirmed_by, action.confirmed_at = principal.user_id, now
    action.status, action.executed_at = "executed", now
    action.result = {
        "work_item_id": item.id,
        "work_item_key": item.tracker_key,
        "project": project.slug,
        "workflow": binding.workflow,
        "url": _console(f"/p/{project.slug}/items/{item.id}"),
    }
    await journal(
        session,
        record,
        "action.confirmed",
        {
            "action_id": action.id,
            "status": action.status,
            "work_item": item.tracker_key,
            "project": project.slug,
        },
    )
    await audit(session, principal, "assistant.launch.confirm", org_id=record.org_id,
                target_type="conversation_action", target_id=action.id, conversation=record.id,
                project=project.slug, workflow=binding.workflow, work_item=item.id)  # fmt: skip
    record.messages = [
        *(record.messages or []),
        {
            "role": "system",
            "content": f"The person confirmed the action `{action.id}` ({action.summary}): the work item "
            f"{item.tracker_key} was started in `{project.slug}`. `launch_status` says what becomes of it.",
        },
    ]
    await session.flush()
    return action


async def _installation_slug(session: AsyncSession, record: AgentSessionRecord) -> str:
    row = await session.get(AssistantInstallation, record.installation_id) if record.installation_id else None
    return row.slug if row is not None else "?"


async def _status(session: AsyncSession, record: AgentSessionRecord, action_id: object) -> str:
    from ..knowledge.marked import readable
    from .artifacts import artifacts_of, to_contract

    query = select(ConversationAction).where(
        ConversationAction.conversation_id == record.id, ConversationAction.kind == "launch"
    )
    if isinstance(action_id, str) and action_id:
        query = query.where(ConversationAction.id == action_id)
    actions = list((await session.execute(query.order_by(ConversationAction.created_at))).scalars())
    if not actions:
        return "No workflow was proposed in this conversation" + (f" as `{action_id}`." if action_id else ".")
    person = await person_of(session, record)
    organisation = await session.get(Organization, record.org_id)
    org_slug = organisation.slug if organisation is not None else ""
    lines: list[dict[str, Any]] = []
    for action in actions:
        entry: dict[str, Any] = {"action_id": action.id, "launch": action.need, "status": action.status}
        result = action.result or {}
        item = await session.get(WorkItem, str(result.get("work_item_id") or "")) if result else None
        if action.status != "executed" or item is None:
            lines.append(entry)
            continue
        project = await session.get(Project, item.project_id)
        entry["work_item"] = item.tracker_key
        session.add(_journal_line(record, action.need, "status", item.tracker_key))
        if (
            person is None
            or project is None
            or not person.can(Permission.PROJECT_READ, org_slug, project.slug)
            or not await readable(session, person, item, record.org_id)
        ):
            entry["readable"] = False
            entry["note"] = "the person can no longer read this work item"
            lines.append(entry)
            continue
        latest: dict[str, Any] = {}
        for revision in await artifacts_of(session, item.id):
            try:  # a revision the contract cannot read is left out, never the whole report
                latest[revision.name] = to_contract(revision)
            except ValueError:
                continue
        entry |= {
            "title": item.title,
            "state": item.state,
            "ended": item.closed_at is not None,
            "url": _console(f"/p/{project.slug}/items/{item.id}"),
            "artifacts": [
                {
                    "name": artifact.name,
                    "version": artifact.version,
                    "type": artifact.type,
                    "shown_as": artifact.display.type if artifact.display is not None else None,
                    "content_url": _console(
                        f"/api/v1/work-items/{item.id}/artifacts/{artifact.name}@{artifact.version}/content"
                    ),
                }
                for artifact in latest.values()
            ],
        }
        lines.append(entry)
    return json.dumps({"launches": lines}, ensure_ascii=False)
