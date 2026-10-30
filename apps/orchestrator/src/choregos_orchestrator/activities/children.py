# SPDX-License-Identifier: Apache-2.0
"""Fan-out and join (S38-12, ADR 0057): one child work item per element of a list, then wait.

A system transition that declares `spawn:` opens a child work item for each element of a list the
parent holds — `fields.<name>`, or one of its outputs, `outputs.metrics.relevant` — in the workflow
it names, and passes only when every child is SETTLED: closed, in a terminal state or stopped.

- The child's key is the parent's with its rank (`RESEARCH-12.1`, `RESEARCH-12.2`): a retried
  activity, a parent replayed after a crash, find the children already there and open none twice.
  The last segment is not a number, so the platform's own numbering (`<PREFIX>-<n>`) never counts it.
- The child keeps its parent in `documents.parent`; closing, it signals `child_settled` to the
  parent's interpreter (`notify_parent`, from `close_out`). The parent also re-reads its children
  every `CHILDREN_REREAD` in case a signal is lost.
- The join writes `documents.children` on the parent — each child's key, title and final state,
  and the count per state — so a later step reads what the children concluded.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from choregos_api.db.models import WorkItem
from choregos_api.errors import ApiError
from choregos_api.logging import get_logger
from choregos_contracts import SpawnSpec
from sqlalchemy import select
from temporalio import activity

from .base import db, load_work_item, project_bundle

logger = get_logger("choregos.children")

#: The document a child keeps of its parent, and the one the join writes on the parent.
PARENT = "parent"
CHILDREN = "children"


class SpawnRefusedError(ValueError):
    """The list cannot be read, or holds more elements than `max_children`."""


def child_key(parent_key: str, rank: int) -> str:
    return f"{parent_key}.{rank}"


def _as_value(raw: Any) -> Any:
    """An output is kept as a value or as its JSON text: both read the same."""
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(raw)
    return raw


def outputs_of(documents: dict[str, Any]) -> dict[str, Any]:
    return {name: _as_value(value) for name, value in (documents or {}).items()}


def items_of(expression: str, fields: dict[str, Any], documents: dict[str, Any]) -> list[Any]:
    """The list `for_each` names: `fields.<name>` or `outputs.<name>`, then keys into it."""
    root, *path = expression.split(".")
    value: Any = dict(fields or {}) if root == "fields" else outputs_of(documents)
    walked = [root]
    for key in path:
        walked.append(key)
        if not isinstance(value, dict) or key not in value:
            raise SpawnRefusedError(f"`{'.'.join(walked)}` does not exist")
        value = value[key]
    if not isinstance(value, list):
        raise SpawnRefusedError(f"`{expression}` is not a list")
    return value


def render(value: Any, context: dict[str, Any]) -> Any:
    """Strings rendered in sandboxed Jinja, recursively. A string that is ONE expression,
    `{{ item.dsr }}`, keeps the expression's type: a number stays a number in the child's fields."""
    from jinja2 import StrictUndefined, TemplateError
    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment(undefined=StrictUndefined)
    try:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("{{") and text.endswith("}}") and text.count("{{") == 1:
                return env.compile_expression(text[2:-2].strip(), undefined_to_none=False)(**context)
            if "{{" in value or "{%" in value:
                return env.from_string(value).render(**context)
            return value
    except TemplateError as refused:  # an absent variable, a syntax error, an escape attempt
        raise SpawnRefusedError(f"template refused: {refused}") from refused
    if isinstance(value, dict):
        return {key: render(v, context) for key, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


@activity.defn(name="spawn_children")
async def spawn_children(payload: dict[str, Any]) -> dict[str, Any]:
    """Open the children the parent does not have yet, then start their interpreters.

    Returns `{children: [keys]}`, or `{refused: why}` when the list cannot be read or a child is
    refused (a workflow that does not exist, fields its schema refuses): every retry would be
    refused the same way, so it goes to the transition's `on_fail`, not to a retry."""
    from choregos_api.rbac import SYSTEM
    from choregos_api.schemas.tickets import WorkItemCreate
    from choregos_api.services.initiator import initiator
    from choregos_api.services.tickets import creer_un_ticket
    from choregos_api.temporal import interpreter_id
    from temporalio.exceptions import WorkflowAlreadyStartedError

    spec = SpawnSpec.model_validate(payload["spawn"])
    to_start: list[dict[str, Any]] = []
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        parent = await load_work_item(session, payload["work_item_id"])
        try:
            elements = items_of(spec.for_each, parent.fields, parent.documents)
            if len(elements) > spec.max_children:
                raise SpawnRefusedError(
                    f"{len(elements)} children, more than `max_children` ({spec.max_children})"
                )
        except SpawnRefusedError as refused:
            return {"refused": str(refused)}
        keys: list[str] = []
        outputs = outputs_of(parent.documents)
        for rank, element in enumerate(elements, start=1):
            key = child_key(parent.tracker_key, rank)
            keys.append(key)
            child = (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == bundle.project.id, WorkItem.tracker_key == key
                    )
                )
            ).scalar_one_or_none()
            if child is None:
                context = {
                    "item": element,
                    "rank": rank,
                    "fields": dict(parent.fields or {}),
                    "outputs": outputs,
                    "parent": {"key": parent.tracker_key, "title": parent.title},
                }
                try:
                    child = await creer_un_ticket(
                        session,
                        SYSTEM,
                        bundle.project,
                        WorkItemCreate(
                            title=str(render(spec.title, context))[:500],
                            fields=render(spec.fields, context),
                            workflow=spec.workflow,
                            labels=[f"parent:{parent.tracker_key}"],
                            start=False,
                        ),
                        canal="fan-out",
                        cle=key,
                        initiated_by=initiator("work_item", parent.tracker_key),
                        # Its title and fields come from the parent's outputs (ADR 0114 §4).
                        restricted_knowledge=list(parent.restricted_knowledge or []),
                    )
                except (SpawnRefusedError, ApiError) as refused:
                    detail = refused.detail if isinstance(refused, ApiError) else str(refused)
                    logger.error("child refused", project=bundle.slug, work_item=parent.tracker_key, key=key)
                    return {"refused": f"child {key}: {detail}"}
                child.documents = {
                    **(child.documents or {}),
                    PARENT: {
                        "work_item_id": parent.id,
                        "key": parent.tracker_key,
                        "workflow_id": str(payload["workflow_id"]),
                    },
                }
                logger.info("child opened", project=bundle.slug, work_item=parent.tracker_key, key=key)
            if child.closed_at is None:
                to_start.append(
                    {
                        "id": interpreter_id(bundle.slug, key),
                        "input": {
                            "project_id": bundle.project.id,
                            "project_slug": bundle.slug,
                            "work_item_id": child.id,
                            "tracker_key": key,
                        },
                    }
                )
                child.temporal_wf_id = interpreter_id(bundle.slug, key)
    # Started after the commit: a child's interpreter reads its work item at once. Same identifier
    # every time, so a retried activity finds the interpreter already running.
    for start in to_start:
        with contextlib.suppress(WorkflowAlreadyStartedError):
            await activity.client().start_workflow(
                "WorkflowInterpreter",
                start["input"],
                id=start["id"],
                task_queue=activity.info().task_queue,
            )
    return {"children": keys}


@activity.defn(name="children_state")
async def children_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Where the children are. When none is left open, the join: `documents.children` on the parent,
    and whether enough children ended in a `succeeded` state (`min_succeeded`)."""
    spec = SpawnSpec.model_validate(payload["spawn"])
    keys = [str(k) for k in payload["children"]]
    async with db() as session:
        parent = await load_work_item(session, payload["work_item_id"])
        rows = (
            (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == parent.project_id, WorkItem.tracker_key.in_(keys)
                    )
                )
            )
            .scalars()
            .all()
        )
        by_key = {row.tracker_key: row for row in rows}
        pending = [k for k in keys if k not in by_key or by_key[k].closed_at is None]
        if pending:
            return {"pending": pending, "settled": len(keys) - len(pending)}
        children: list[dict[str, Any]] = [
            {"key": k, "title": by_key[k].title, "state": by_key[k].state, "fields": by_key[k].fields}
            for k in keys
        ]
        states: dict[str, int] = {}
        for child in children:
            states[child["state"]] = states.get(child["state"], 0) + 1
        succeeded = sum(n for state, n in states.items() if state in spec.succeeded)
        passed = succeeded >= spec.min_succeeded
        parent.documents = {
            **(parent.documents or {}),
            CHILDREN: {"total": len(children), "succeeded": succeeded, "states": states, "items": children},
        }
    detail = f"{len(children)} children settled ({', '.join(f'{s}: {n}' for s, n in sorted(states.items()))})"
    if not passed:
        detail += f"; {succeeded} succeeded, {spec.min_succeeded} required"
    return {"pending": [], "passed": passed, "detail": detail}


def parent_of(item: WorkItem) -> dict[str, str] | None:
    """What `notify_parent` needs of a closing child, read while its session is open; `None` when the
    work item was not opened by a fan-out."""
    parent = (item.documents or {}).get(PARENT)
    if not isinstance(parent, dict) or not parent.get("workflow_id"):
        return None
    return {"workflow_id": str(parent["workflow_id"]), "key": item.tracker_key, "state": item.state}


async def notify_parent(settled: dict[str, str]) -> None:
    """`child_settled` to the parent's interpreter, once the child is closed and committed: the parent
    re-reads its children at once instead of at its next re-read. A parent that stopped listening is
    not a failure of the child."""
    try:
        await (
            activity.client()
            .get_workflow_handle(settled["workflow_id"])
            .signal("child_settled", {"key": settled["key"], "state": settled["state"]})
        )
    except Exception as error:
        logger.warning(
            "parent not notified", workflow_id=settled["workflow_id"], key=settled["key"], error=str(error)
        )
