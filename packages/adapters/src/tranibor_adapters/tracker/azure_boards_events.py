# SPDX-License-Identifier: Apache-2.0
"""Normalization of Azure DevOps Service Hooks (Boards) into `InboundEvent`.

A service hook posts an `eventType` (`workitem.created`, `workitem.updated`, `workitem.commented`,
`workitem.deleted`) and a `resource`. For an update, `resource.fields` holds only what moved, as
`{oldValue, newValue}` pairs, and `resource.revision.fields` the whole item: that is where a state
change or a tag added is read, the equivalent of a moved card. The shapes follow Microsoft's
"Service hooks events" reference; they are exercised here against payloads written from it, not
against a live organisation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from tranibor_contracts import InboundEvent, InboundEventType

from .azure_html import html_to_text
from .github_events import parse_command

#: State names the four standard processes (Agile, Scrum, CMMI, Basic) share, by Jira-style category:
#: the payload carries no state category, and a state a team added is left unclassified on purpose (an
#: unclassified move never brings an ended work item back).
_DONE = frozenset({"closed", "done", "removed", "completed", "resolved"})
_NEW = frozenset({"new", "to do", "proposed", "design"})
_WORKING = frozenset({"active", "doing", "committed", "approved", "in progress"})


def parse_azure_boards_event(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """Translates a service-hook delivery into zero, one or several `InboundEvent`."""
    handler = _HANDLERS.get(str(payload.get("eventType", "")))
    if handler is None:
        return []
    return handler(delivery_id or str(payload.get("id", "")), payload)


def tags_of(raw: Any) -> list[str]:
    """Azure keeps tags as one string, `a; b`."""
    return [tag.strip() for tag in str(raw or "").split(";") if tag.strip()]


def identity_text(raw: Any) -> str | None:
    """An Azure identity in words: `Display Name <unique name>`, the unique name being the sign-in
    address (an e-mail on Azure DevOps Services, `DOMAIN\\user` on a Server)."""
    if isinstance(raw, str):
        return raw.strip() or None
    found = raw if isinstance(raw, dict) else {}
    name = str(found.get("displayName") or "").strip()
    unique = str(found.get("uniqueName") or "").strip()
    if name and unique and unique != name:
        return f"{name} <{unique}>"
    return name or unique or None


def status_category(state: Any) -> str | None:
    """`new`, `indeterminate` or `done` for the states every standard process shares, else `None`."""
    name = str(state or "").strip().casefold()
    if name in _DONE:
        return "done"
    if name in _NEW:
        return "new"
    return "indeterminate" if name in _WORKING else None


def _resource(payload: dict[str, Any]) -> dict[str, Any]:
    found = payload.get("resource")
    return found if isinstance(found, dict) else {}


def _fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The item as it stands: the revision's fields for an update, the resource's for a creation."""
    resource = _resource(payload)
    revision = resource.get("revision")
    if isinstance(revision, dict) and isinstance(revision.get("fields"), dict):
        return dict(revision["fields"])
    fields = resource.get("fields")
    return (
        {k: (v["newValue"] if isinstance(v, dict) and "newValue" in v else v) for k, v in fields.items()}
        if isinstance(fields, dict)
        else {}
    )


def _item_id(payload: dict[str, Any]) -> str | None:
    resource = _resource(payload)
    found = resource.get("workItemId") or resource.get("id")
    return str(found) if found not in (None, "") else None


def _url(payload: dict[str, Any], fields: dict[str, Any], key: str | None) -> str | None:
    collection = ((payload.get("resourceContainers") or {}).get("collection") or {}).get("baseUrl")
    project = fields.get("System.TeamProject")
    if collection and project and key:
        return f"{str(collection).rstrip('/')}/{quote(str(project))}/_workitems/edit/{key}"
    html_link = ((_resource(payload).get("_links") or {}).get("html") or {}).get("href")
    return str(html_link) if html_link else None


def _base(delivery_id: str, payload: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    key = _item_id(payload)
    resource = _resource(payload)
    project = fields.get("System.TeamProject")
    return {
        "source": "azure-boards",
        "delivery_id": delivery_id,
        # The Tranibor project carries the project's name, in lower case, as a fallback: the API routes
        # by the tracker connector first.
        "project_slug": str(project).lower() if project else None,
        "work_item_key": key,
        "actor": identity_text(resource.get("revisedBy") or fields.get("System.ChangedBy")),
    }


def _body(payload: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    state = fields.get("System.State")
    return {
        "title": fields.get("System.Title"),
        "body": html_to_text(fields.get("System.Description")),
        "url": _url(payload, fields, _item_id(payload)),
        "labels": tags_of(fields.get("System.Tags")),
        "item_type": fields.get("System.WorkItemType"),
        "state": state,
        "area_path": fields.get("System.AreaPath"),
        "status_category": status_category(state),
        "reporter": identity_text(fields.get("System.CreatedBy")),
    }


def _created(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    fields = _fields(payload)
    return [
        InboundEvent(
            type=InboundEventType.ITEM_CREATED,
            payload=_body(payload, fields),
            **_base(delivery_id, payload, fields),
        )
    ]


def _deleted(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    fields = _fields(payload)
    return [
        InboundEvent(
            type=InboundEventType.ITEM_CLOSED,
            payload=_body(payload, fields),
            **_base(delivery_id, payload, fields),
        )
    ]


def _updated(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """One event per moved field the platform follows: the state, the tags."""
    fields = _fields(payload)
    common = _base(delivery_id, payload, fields)
    body = _body(payload, fields)
    changes = _resource(payload).get("fields")
    changes = changes if isinstance(changes, dict) else {}
    events: list[InboundEvent] = []
    state = changes.get("System.State")
    if isinstance(state, dict):
        events.append(
            InboundEvent(
                type=InboundEventType.ITEM_MOVED,
                payload={
                    **body,
                    "field": "status",
                    "from_status": state.get("oldValue"),
                    "to_status": state.get("newValue"),
                },
                **common,
            )
        )
    tags = changes.get("System.Tags")
    if isinstance(tags, dict):
        before, after = set(tags_of(tags.get("oldValue"))), tags_of(tags.get("newValue"))
        added = sorted(set(after) - before)
        events.append(
            InboundEvent(
                type=InboundEventType.ITEM_LABELED,
                payload={**body, "labels": after, "label": added[0] if added else None, "removed": not added},
                **common,
            )
        )
    if not events:
        events.append(InboundEvent(type=InboundEventType.ITEM_UPDATED, payload=body, **common))
    return events


def _comment_text(payload: dict[str, Any], fields: dict[str, Any]) -> str:
    resource = _resource(payload)
    raw = resource.get("comment")
    if isinstance(raw, dict):
        raw = raw.get("text")
    if not raw:
        raw = fields.get("System.History")
    if not raw:
        moved = (resource.get("fields") or {}).get("System.History")
        raw = moved.get("newValue") if isinstance(moved, dict) else moved
    return html_to_text(raw)


def _commented(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    fields = _fields(payload)
    common = _base(delivery_id, payload, fields)
    text = _comment_text(payload, fields)
    author = common["actor"]
    events = [
        InboundEvent(
            type=InboundEventType.ITEM_COMMENTED,
            payload={"body": text, "author": author, "url": _url(payload, fields, common["work_item_key"])},
            **common,
        )
    ]
    command = parse_command(text)
    if command:
        events.append(
            InboundEvent(
                type=InboundEventType.HUMAN_DECISION,
                payload={
                    "verb": command["verb"],
                    "argument": command["argument"],
                    "channel": "tracker",
                    "by": author,
                },
                **common,
            )
        )
    return events


_HANDLERS = {
    "workitem.created": _created,
    "workitem.updated": _updated,
    "workitem.deleted": _deleted,
    "workitem.commented": _commented,
}
