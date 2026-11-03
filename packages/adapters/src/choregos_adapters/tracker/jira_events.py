# SPDX-License-Identifier: Apache-2.0
"""Normalization of Jira Cloud webhooks into `InboundEvent`.

Jira sends a `webhookEvent` (`jira:issue_created`, `jira:issue_updated`, `comment_created`)
and, for updates, a `changelog` that says what moved. That is where a status change or the
addition of a label is read: Jira's equivalent of a moved card.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import InboundEvent, InboundEventType

from .adf import text_of_adf
from .github_events import parse_command


def parse_jira_event(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """Translates a Jira webhook into zero, one or several `InboundEvent`."""
    kind = str(payload.get("webhookEvent", ""))
    handler = _HANDLERS.get(kind)
    if handler is None:
        return []
    return handler(delivery_id or str(payload.get("id", "")), payload)


def _issue(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("issue", {}) or {}


def _fields(payload: dict[str, Any]) -> dict[str, Any]:
    return _issue(payload).get("fields", {}) or {}


def _base(delivery_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user = payload.get("user", {}) or {}
    issue = _issue(payload)
    key = issue.get("key")
    return {
        "source": "jira",
        "delivery_id": delivery_id,
        # The Choregos project carries the same slug as the Jira project key, in lower case.
        "project_slug": str(key).split("-")[0].lower() if key else None,
        "work_item_key": key,
        "actor": user.get("displayName") or user.get("accountId"),
    }


def person_of(user: dict[str, Any] | None) -> str | None:
    """A Jira user in words: `Display Name <e-mail>`, or the name alone when the site hides the
    e-mail (Jira Cloud does, per the user's profile visibility)."""
    user = user or {}
    name = str(user.get("displayName") or "").strip()
    email = str(user.get("emailAddress") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email or None


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    fields = _fields(payload)
    issue = _issue(payload)
    return {
        "title": fields.get("summary"),
        "body": text_of_adf(fields.get("description")),
        "url": issue.get("self"),
        "labels": list(fields.get("labels") or []),
        "item_type": ((fields.get("issuetype") or {}).get("name")),
        "state": ((fields.get("status") or {}).get("name")),
        # Who filed the issue: an agent whose rules depend on the requester reads it (#1179).
        "reporter": person_of(fields.get("reporter")),
    }


def _created(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    return [
        InboundEvent(
            type=InboundEventType.ITEM_CREATED, payload=_body(payload), **_base(delivery_id, payload)
        )
    ]


def _deleted(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    return [
        InboundEvent(type=InboundEventType.ITEM_CLOSED, payload=_body(payload), **_base(delivery_id, payload))
    ]


def _updated(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """One `changelog` entry per modified field: status, labels, resolution."""
    common = _base(delivery_id, payload)
    body = _body(payload)
    items = ((payload.get("changelog") or {}).get("items")) or []
    events: list[InboundEvent] = []
    for item in items:
        field = str(item.get("field", "")).lower()
        if field == "status":
            events.append(
                InboundEvent(
                    type=InboundEventType.ITEM_MOVED,
                    payload={
                        **body,
                        "field": "status",
                        "from_status": item.get("fromString"),
                        "to_status": item.get("toString"),
                    },
                    **common,
                )
            )
        elif field == "labels":
            added = _added_labels(item)
            events.append(
                InboundEvent(
                    type=InboundEventType.ITEM_LABELED,
                    payload={
                        **body,
                        "labels": str(item.get("toString") or "").split(),
                        "label": added[0] if added else None,
                        "removed": not added,
                    },
                    **common,
                )
            )
        elif field == "resolution" and item.get("toString"):
            events.append(InboundEvent(type=InboundEventType.ITEM_CLOSED, payload=body, **common))
    if not events:
        events.append(InboundEvent(type=InboundEventType.ITEM_UPDATED, payload=body, **common))
    return events


def _added_labels(item: dict[str, Any]) -> list[str]:
    before = set(str(item.get("fromString") or "").split())
    after = set(str(item.get("toString") or "").split())
    return sorted(after - before)


def _commented(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    comment = payload.get("comment", {}) or {}
    author = (comment.get("author") or {}).get("displayName")
    text = text_of_adf(comment.get("body"))
    common = _base(delivery_id, payload)
    events = [
        InboundEvent(
            type=InboundEventType.ITEM_COMMENTED,
            payload={"body": text, "author": author, "url": comment.get("self")},
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
    "jira:issue_created": _created,
    "jira:issue_updated": _updated,
    "jira:issue_deleted": _deleted,
    "comment_created": _commented,
    "comment_updated": _commented,
}
