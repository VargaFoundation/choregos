"""Normalisation des webhooks GitLab en `InboundEvent`.

GitLab annonce le type dans `object_kind` (`issue`, `note`, `merge_request`, `pipeline`) et
décrit ce qui a changé dans `changes`. Les labels scopés `choregos::<état>` y arrivent comme
des labels ordinaires : c'est le passage d'un label à l'autre qui signale le déplacement.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import InboundEvent, InboundEventType

from .github_events import parse_command


def parse_gitlab_event(event_name: str, delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """Traduit un webhook GitLab en zéro, un ou plusieurs `InboundEvent`."""
    kind = str(payload.get("object_kind") or event_name).lower()
    handler = _HANDLERS.get(kind)
    if handler is None:
        return []
    return handler(delivery_id, payload)


def _project_path(payload: dict[str, Any]) -> str | None:
    project = payload.get("project", {}) or {}
    path = project.get("path_with_namespace")
    return str(path) if path else None


def _project_slug(payload: dict[str, Any]) -> str | None:
    path = _project_path(payload)
    return path.split("/")[-1] if path else None


def _base(delivery_id: str, payload: dict[str, Any], key: str | None) -> dict[str, Any]:
    user = payload.get("user", {}) or {}
    return {
        "source": "gitlab",
        "delivery_id": delivery_id or str(payload.get("object_attributes", {}).get("id", "")),
        "project_slug": _project_slug(payload),
        "work_item_key": key,
        "actor": user.get("username"),
    }


def _labels(payload: dict[str, Any]) -> list[str]:
    return [label.get("title", "") for label in payload.get("labels", []) if label.get("title")]


def _issue(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    attributes = payload.get("object_attributes", {}) or {}
    key = f"{_project_path(payload)}#{attributes.get('iid')}"
    common = _base(delivery_id, payload, key)
    body = {
        "title": attributes.get("title"),
        "body": attributes.get("description") or "",
        "url": attributes.get("url"),
        "labels": _labels(payload),
        "state": attributes.get("state"),
    }
    action = attributes.get("action")
    if action == "open":
        return [InboundEvent(type=InboundEventType.ITEM_CREATED, payload=body, **common)]
    if action == "close":
        return [InboundEvent(type=InboundEventType.ITEM_CLOSED, payload=body, **common)]
    added = _added_labels(payload)
    if added:
        return [
            InboundEvent(
                type=InboundEventType.ITEM_LABELED,
                payload={**body, "label": added[0], "removed": False},
                **common,
            )
        ]
    return [InboundEvent(type=InboundEventType.ITEM_UPDATED, payload=body, **common)]


def _added_labels(payload: dict[str, Any]) -> list[str]:
    changes = (payload.get("changes") or {}).get("labels") or {}
    before = {label.get("title") for label in changes.get("previous", [])}
    after = [label.get("title") for label in changes.get("current", [])]
    return [title for title in after if title and title not in before]


def _note(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    attributes = payload.get("object_attributes", {}) or {}
    issue = payload.get("issue", {}) or {}
    if not issue:
        return []  # une note sur une MR ou un commit n'est pas une décision de ticket
    key = f"{_project_path(payload)}#{issue.get('iid')}"
    common = _base(delivery_id, payload, key)
    text = attributes.get("note") or ""
    author = (payload.get("user") or {}).get("username")
    events = [
        InboundEvent(
            type=InboundEventType.ITEM_COMMENTED,
            payload={"body": text, "author": author, "url": attributes.get("url")},
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


def _merge_request(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    attributes = payload.get("object_attributes", {}) or {}
    key = _work_item_from_branch(attributes.get("source_branch", ""), payload)
    common = _base(delivery_id, payload, key)
    body = {
        "number": attributes.get("iid"),
        "pr_url": attributes.get("url"),
        "sha": (attributes.get("last_commit") or {}).get("id"),
        "base": attributes.get("target_branch"),
        "head": attributes.get("source_branch"),
        "draft": bool(attributes.get("work_in_progress")),
        "labels": _labels(payload),
        "merged": attributes.get("state") == "merged",
    }
    action = attributes.get("action")
    if action == "open":
        return [InboundEvent(type=InboundEventType.PR_OPENED, payload=body, **common)]
    if action == "update":
        return [InboundEvent(type=InboundEventType.PR_SYNCHRONIZED, payload=body, **common)]
    if action == "merge":
        return [InboundEvent(type=InboundEventType.PR_MERGED, payload=body, **common)]
    if action == "close":
        return [InboundEvent(type=InboundEventType.PR_CLOSED, payload=body, **common)]
    return []


def _pipeline(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    attributes = payload.get("object_attributes", {}) or {}
    status = str(attributes.get("status", "")).lower()
    if status not in {"success", "failed", "running"}:
        return []
    key = _work_item_from_branch(attributes.get("ref", ""), payload)
    common = _base(delivery_id, payload, key)
    kind = {
        "success": InboundEventType.CI_SUCCEEDED,
        "failed": InboundEventType.CI_FAILED,
        "running": InboundEventType.CI_STARTED,
    }[status]
    return [
        InboundEvent(
            type=kind,
            payload={
                "conclusion": status,
                "sha": attributes.get("sha"),
                "branch": attributes.get("ref"),
                "url": (payload.get("project") or {}).get("web_url"),
                "pipeline_id": attributes.get("id"),
            },
            **common,
        )
    ]


def _work_item_from_branch(branch: str, payload: dict[str, Any]) -> str | None:
    """`choregos/123-slug` désigne le ticket #123 du projet."""
    import re

    match = re.search(r"choregos/(?P<number>\d+)", branch or "")
    if match is None:
        return None
    return f"{_project_path(payload)}#{match.group('number')}"


_HANDLERS = {
    "issue": _issue,
    "note": _note,
    "merge_request": _merge_request,
    "pipeline": _pipeline,
}
