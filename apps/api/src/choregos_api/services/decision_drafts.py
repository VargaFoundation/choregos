# SPDX-License-Identifier: Apache-2.0
"""A person's client prepares their decision; the person decides (ADR 0163).

A request waits for a person (ADR 0035 point 5, ADR 0153). Through the MCP door their client — Claude Code,
an assistant reached from claude.ai — reads that request and deposits a DRAFT of its decision on the row
(`human_requests.draft`). A draft decides nothing: the request keeps waiting, nothing reaches the workflow's
interpreter, no `workitem.decision` is written. The person takes it into the decision's form in the console,
or discards it; the decision is the one route's, `POST /work-items/{id}/decisions`, under their session.

Why on the request and not a `ConversationAction` (S63-10): that row belongs to a conversation the door's
client does not have, its confirmation calls a source through the broker while a decision must stay on the
one decision route, and its life is a day where a request waits as long as its step (ADR 0163 point 5).

"The caller's own request" (point 3): waiting, on a work item the person may read (ADR 0114), in a project
where they hold `item:decide`, that ADR 0153's rules let them answer THEMSELVES — never through an
organisation administrator's override. Anything else is not found.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import jsonschema
from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HumanRequest, Organization, Project, WorkItem
from ..knowledge.marked import readable
from ..rbac import Permission, Principal
from .human_assignee import assignee_of_the_request, decider_verdict, sla_passed
from .personal_spaces import listed

#: What a draft may propose, by the request's kind (the decision route's own rules, `post_decision`).
KINDS: dict[str, tuple[str, ...]] = {
    "approval": ("approve", "reject"),
    "scope_change": ("approve", "reject"),
    "question": ("answer",),
    "task": ("complete", "reject"),
}
SUMMARY_CHARS = 300
TEXT_CHARS = {"answer": 20_000, "reason": 2_000, "note": 2_000}


class DraftRefused(Exception):  # noqa: N818 - a refusal told to the client, not a failure
    """Why a draft is not deposited, in words the client can act on."""


@dataclass(frozen=True, slots=True)
class OwnRequest:
    """A waiting request the person may answer themselves, with what it belongs to."""

    request: HumanRequest
    item: WorkItem
    project: Project
    org: str


async def may_answer(
    session: AsyncSession, principal: Principal, item: WorkItem, project: Project, org: str, row: HumanRequest
) -> str:
    """`ok` (the person answers it themselves), `override` (an organisation administrator the step is not
    addressed to) or `refused` — the decision route's verdict, rank included."""
    if not principal.can(Permission.ITEM_DECIDE, org, project.slug):
        return "refused"
    assignee = await assignee_of_the_request(session, item, row)
    return decider_verdict(principal, org, assignee, after_sla=sla_passed(row))


async def own_requests(
    session: AsyncSession, principal: Principal, *, work_item_id: str | None = None, limit: int = 50
) -> list[OwnRequest]:
    """The waiting requests the person may answer themselves, oldest first."""
    orgs = sorted(set(principal.org_roles) | {key.split("/", 1)[0] for key in principal.project_roles})
    if not orgs:
        return []
    query = (
        select(HumanRequest, WorkItem, Project, Organization.slug)
        .join(WorkItem, HumanRequest.work_item_id == WorkItem.id)
        .join(Project, HumanRequest.project_id == Project.id)
        .join(Organization, Project.org_id == Organization.id)
        .where(HumanRequest.decided_at.is_(None), Organization.slug.in_(orgs), listed())
        .order_by(HumanRequest.requested_at, HumanRequest.id)
    )
    if work_item_id is not None:
        query = query.where(WorkItem.id == work_item_id)
    found: list[OwnRequest] = []
    for row, item, project, org in (await session.execute(query)).all():
        if not principal.can(Permission.PROJECT_READ, org, project.slug):
            continue
        if await may_answer(session, principal, item, project, org, row) != "ok":
            continue
        if not await readable(session, principal, item, project.org_id):
            continue  # the request is the run's content (ADR 0114)
        found.append(OwnRequest(row, item, project, org))
        if len(found) >= limit:
            break
    return found


def _text(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DraftRefused(f"`{key}` must be a string")
    value = value.strip()
    if len(value) > TEXT_CHARS[key]:
        raise DraftRefused(f"`{key}` is longer than {TEXT_CHARS[key]} characters")
    return value or None


def _values(row: HumanRequest, values: object) -> dict[str, Any]:
    """A task's values: no key outside its form, each value valid for its property; required fields may be
    left for the person (a draft is partial by nature)."""
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise DraftRefused("`values` must be an object of the task's form fields")
    form = dict((row.payload or {}).get("form") or {})
    properties = dict(form.get("properties") or {})
    outside = sorted(set(values) - set(properties))
    if outside:
        raise DraftRefused(f"not in the task's form: {', '.join(outside)}")
    partial = {**form, "required": []}
    errors = sorted(
        jsonschema.Draft202012Validator(partial).iter_errors(values), key=lambda error: list(error.path)
    )
    if errors:
        where = ".".join(str(part) for part in errors[0].path) or "values"
        raise DraftRefused(f"`{where}`: {errors[0].message}")
    return dict(values)


def summary_of(kind: str, answer: str | None, reason: str | None, values: dict[str, Any]) -> str:
    detail = answer or reason or (json.dumps(values, ensure_ascii=False) if values else "")
    text = f"{kind}: {detail}" if detail else kind
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "..."


def draft_of(row: HumanRequest, arguments: dict[str, Any], *, by: str, via: dict[str, Any]) -> dict[str, Any]:
    """The draft the arguments propose for this request, checked; `DraftRefused` otherwise."""
    allowed = KINDS.get(row.kind, ())
    kind = arguments.get("kind")
    if kind not in allowed:
        raise DraftRefused(
            f"a {row.kind} request takes a draft of kind {' or '.join(f'`{k}`' for k in allowed)}, "
            f"not `{kind}`"
        )
    answer, reason, note = _text(arguments, "answer"), _text(arguments, "reason"), _text(arguments, "note")
    if kind == "answer" and not answer:
        raise DraftRefused("an `answer` draft carries the `answer`")
    values = _values(row, arguments.get("values")) if kind == "complete" else {}
    if kind != "complete" and arguments.get("values") is not None:
        raise DraftRefused("only a `complete` draft carries `values`")
    return {
        "kind": kind,
        "answer": answer if kind == "answer" else None,
        "reason": reason if kind == "reject" else None,
        "values": values if kind == "complete" else None,
        "note": note,
        "summary": summary_of(kind, answer if kind == "answer" else None, reason, values),
        "by": by,
        "via": via,
        "at": utcnow().isoformat(),
    }


def deposit(row: HumanRequest, draft: dict[str, Any]) -> None:
    """One draft per request: a new one replaces it; a decided request takes none."""
    if row.decided_at is not None:
        raise DraftRefused("this request has already been decided")
    row.draft = draft


def compared(draft: dict[str, Any] | None, kind: str, body: dict[str, Any]) -> str | None:
    """What the decision's audit says of a draft (ADR 0163 point 7): `same`, `changed`, or `None`."""
    if not draft:
        return None
    same = draft.get("kind") == kind
    if kind == "answer":
        same = same and (draft.get("answer") or "") == (body.get("answer") or "")
    elif kind == "complete":
        same = same and (draft.get("values") or {}) == (body.get("values") or {})
    return "same" if same else "changed"
