# SPDX-License-Identifier: Apache-2.0
"""Where a work item is born: the workflow it follows, and the ONLY work item constructor (ADR 0031).

Five paths created a work item (the console, the MCP door, a tracker webhook, the catch-up, the
promoted finding) and two set the `inbox` state by hand: on a workflow that starts elsewhere, the
work item died at birth. They all go through `new_work_item`, which chooses the workflow, sets its
initial state and pins its version together. A test refuses any other construction.
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
from .definitions import active_workflow_named, default_workflow, policy_engine, workflow_model
from .initiator import initiator
from .llm_routing import Decision, Item, ask_the_model, book, routing_record


@dataclass(frozen=True, slots=True)
class WorkItemBirth:
    """What brings a work item to birth, wherever it comes from."""

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
    #: The workflow asked for explicitly; otherwise the routing, otherwise the default.
    workflow: str | None = None
    labels: tuple[str, ...] = ()
    #: The work item's type in its tracker (Jira: `issuetype`), which the routing can read.
    item_type: str | None = None
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    #: The work item's fields, validated by the chosen workflow's `metadata.inputs`.
    fields: dict[str, Any] = field(default_factory=dict)
    #: The budget given at launch (S27-06): only tightens the workflow's and the policy's.
    budget_usd: float | None = None
    #: An id derived by the caller (ADR 0008): an A2A message sent twice finds its work item (ADR 0078).
    item_id: str | None = None
    #: What the tracker knows about the ticket (S51-04), read from the tracker itself, never from a
    #: caller: stored as `fields["tracker"]`, beside the fields the workflow declares.
    tracker: dict[str, Any] | None = None


def _matches(condition: dict[str, Any], labels: set[str], type_: str | None) -> bool:
    """A rule without a condition matches nothing: it would not silently catch everything."""
    any_of = set(condition.get("labels_any") or [])
    all_of = set(condition.get("labels_all") or [])
    expected = condition.get("item_type")
    if not (any_of or all_of or expected):
        return False
    return (not any_of or bool(any_of & labels)) and all_of <= labels and (not expected or expected == type_)


@dataclass(frozen=True, slots=True)
class Routed:
    """The workflow a birth goes to, and why: `explicit`, `rule`, `llm` or `default` (ADR 0116)."""

    row: WorkflowDef | None
    by: Literal["explicit", "rule", "llm", "default"]
    reason: str | None = None
    #: The model's decision, when the project's strategy asked it.
    decision: Decision | None = None


async def route(session: AsyncSession, project: Project, birth: WorkItemBirth) -> Routed:
    """Explicit, then the rules (unless the strategy is `llm`), then the model (unless it is
    `labels`), then the default."""
    if birth.workflow:
        explicit = await active_workflow_named(session, project.id, birth.workflow)
        if explicit is None:
            raise unprocessable(f"workflow `{birth.workflow}` is unknown or inactive in this project")
        return Routed(explicit, "explicit")
    strategy = project.routing_strategy or "labels"
    labels = set(birth.labels)
    if strategy != "llm":
        for index, rule in enumerate(project.workflow_routing or []):
            if _matches(dict(rule.get("when") or {}), labels, birth.item_type):
                target = await active_workflow_named(session, project.id, str(rule.get("workflow")))
                if target is not None:
                    return Routed(target, "rule", f"routing rule {index + 1}")
    if strategy == "labels":
        return Routed(await default_workflow(session, project.id), "default")
    decision = await ask_the_model(
        session,
        project,
        Item(birth.title, birth.body, tuple(birth.labels), birth.item_type),
    )
    if decision.workflow is not None:
        target = await active_workflow_named(session, project.id, decision.workflow)
        if target is not None:
            return Routed(target, "llm", decision.reason, decision)
    return Routed(await default_workflow(session, project.id), "default", decision.reason, decision)


async def choose_workflow(
    session: AsyncSession, project: Project, birth: WorkItemBirth
) -> WorkflowDef | None:
    """Explicit, then the first routing rule that matches, then the default."""
    return (await route(session, project, birth)).row


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


def validate_fields(schema: dict[str, Any] | None, fields: dict[str, Any], workflow: str) -> None:
    """A field outside the schema, or a required field missing: 422, with its path.

    `fields.tracker` is the platform's (S51-04), not the workflow's: it is left out of the check, so a
    workflow whose inputs forbid extra properties still accepts a work item filed from Jira."""
    fields = {k: v for k, v in fields.items() if k != TRACKER_FIELD}
    if schema is None:
        if fields:
            raise unprocessable(f"workflow `{workflow}` declares no field (`metadata.inputs`)")
        return
    import jsonschema

    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(fields), key=lambda e: list(e.path))
    if errors:
        raise unprocessable(
            f"fields refused by workflow `{workflow}`",
            [{"loc": ["fields", *e.path], "msg": e.message} for e in errors],
        )


def record_labels(item: WorkItem, labels: Iterable[str]) -> None:
    """Store the work item's labels in `documents.labels`, in order, without duplicates.

    They served the routing and were then lost: `signal_train` boarded with `labels: []`, and a work
    item labelled `hotfix` never took the express lane (#279). They live in `documents` rather than
    in a column: neither the contract nor the database schema changes. The list is replaced whole,
    not merged: each tracker sends the current state of its labels.
    """
    item.documents = {
        **(item.documents or {}),
        "labels": list(dict.fromkeys(str(e) for e in labels if e)),
    }


async def _budget_at_launch(
    session: AsyncSession, project: Project, workflow: Any, birth: WorkItemBirth
) -> float | None:
    """The work item's own budget (S27-06, ADR 0047): the one given at launch, else the workflow's
    `budget.ticket_usd`. A budget given at launch may only tighten: wider than the workflow's or the
    policy's ticket budget for its size is refused (422), not silently clamped."""
    from_workflow = workflow.budget.ticket_usd if workflow.budget is not None else None
    if birth.budget_usd is None:
        return from_workflow
    caps = {"policy": (await policy_engine(session, project.id)).budget_ticket(birth.size)}
    if from_workflow is not None:
        caps[f"workflow `{workflow.metadata.name}`"] = from_workflow
    for source, cap in caps.items():
        if birth.budget_usd > cap:
            raise unprocessable(
                f"budget_usd {birth.budget_usd:.2f} $ is wider than the {source}'s ticket budget "
                f"({cap:.2f} $): a budget given at launch may only tighten it"
            )
    return birth.budget_usd


async def verify_birth(session: AsyncSession, project: Project, birth: WorkItemBirth) -> None:
    """Refuses (422) a birth that `new_work_item` would refuse: unknown workflow, invalid fields,
    a budget wider than allowed. Nothing is written, so a caller can run it BEFORE a side effect
    it could not take back (the issue created in an external tracker, S39-16)."""
    refuse_reserved_fields(birth.fields)
    row = await choose_workflow(session, project, birth)
    workflow = workflow_model(row)
    validate_fields(workflow.metadata.inputs, birth.fields, workflow.metadata.name)
    await _budget_at_launch(session, project, workflow, birth)


async def new_work_item(session: AsyncSession, project: Project, birth: WorkItemBirth) -> WorkItem:
    """The only constructor: the initial state OF the chosen workflow, and the pin of its version."""
    refuse_reserved_fields(birth.fields)
    routed = await route(session, project, birth)
    row = routed.row
    workflow = workflow_model(row)
    validate_fields(workflow.metadata.inputs, birth.fields, workflow.metadata.name)
    item = WorkItem(  # the only `WorkItem(` of the production code (the single-constructor guard test)
        project_id=project.id,
        tracker_key=birth.tracker_key,
        title=birth.title,
        body_snapshot=birth.body,
        url=birth.url,
        size=birth.size,
        risk=birth.risk,
        state=WorkflowEngine(workflow).initial_state,
        workflow_def_id=row.id if row is not None else None,
        created_by=birth.created_by,
        reporter=birth.reporter,
        initiated_by=birth.initiated_by,
        allowed_paths=list(birth.allowed_paths),
        fields=with_tracker(birth.fields, birth.tracker),
        budget_usd=await _budget_at_launch(session, project, workflow, birth),
    )
    if birth.item_id is not None:
        item.id = birth.item_id
    if birth.labels:
        record_labels(item, birth.labels)
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


async def new_trial_work_item(
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
    (the single-constructor guard test) still holds: a trial is not routed, it only needs a row
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
