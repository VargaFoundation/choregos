"""Normalisation des webhooks GitHub en `InboundEvent`, et commandes `/choregos …`.

C'est ici que le tracker devient une interface : une carte déplacée, un label posé,
un commentaire `/choregos approve` deviennent des événements que l'orchestrateur comprend.
"""

from __future__ import annotations

import re
from typing import Any

from choregos_contracts import InboundEvent, InboundEventType

COMMAND_RE = re.compile(
    r"^\s*/choregos\s+(?P<verb>approve|reject|answer|scope|pause|resume|stop)\b(?P<rest>.*)$", re.I | re.M
)


def parse_command(body: str) -> dict[str, Any] | None:
    """`/choregos approve`, `/choregos reject raison`, `/choregos answer texte`…"""
    match = COMMAND_RE.search(body or "")
    if match is None:
        return None
    verb = match.group("verb").lower()
    rest = match.group("rest").strip()
    return {"verb": verb, "argument": rest}


def _repo_slug(payload: dict[str, Any]) -> str | None:
    repo = payload.get("repository", {}) or {}
    full_name = repo.get("full_name")
    return str(full_name) if full_name else None


def _project_slug(payload: dict[str, Any]) -> str | None:
    full = _repo_slug(payload)
    return full.split("/")[-1] if full else None


def _issue_key(payload: dict[str, Any], issue: dict[str, Any]) -> str:
    return f"{_repo_slug(payload)}#{issue.get('number')}"


def parse_github_event(event_name: str, delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """Traduit un webhook GitHub en zéro, un ou plusieurs `InboundEvent`."""
    handler = _HANDLERS.get(event_name)
    if handler is None:
        return []
    return handler(delivery_id, payload)


def _base(
    delivery_id: str, payload: dict[str, Any], key: str | None, actor_field: str = "sender"
) -> dict[str, Any]:
    sender = payload.get(actor_field, {}) or {}
    return {
        "source": "github",
        "delivery_id": delivery_id,
        "project_slug": _project_slug(payload),
        "work_item_key": key,
        "actor": sender.get("login"),
    }


def _issues(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    issue = payload.get("issue", {})
    key = _issue_key(payload, issue)
    action = payload.get("action")
    labels = [label.get("name") for label in issue.get("labels", []) if label.get("name")]
    common = _base(delivery_id, payload, key)
    body = {
        "title": issue.get("title"),
        "body": issue.get("body") or "",
        "url": issue.get("html_url"),
        "labels": labels,
        "state": issue.get("state"),
    }
    if action == "opened":
        return [InboundEvent(type=InboundEventType.ITEM_CREATED, payload=body, **common)]
    if action == "closed":
        return [InboundEvent(type=InboundEventType.ITEM_CLOSED, payload=body, **common)]
    if action in {"labeled", "unlabeled"}:
        label = (payload.get("label") or {}).get("name")
        return [
            InboundEvent(
                type=InboundEventType.ITEM_LABELED,
                payload={**body, "label": label, "removed": action == "unlabeled"},
                **common,
            )
        ]
    return [InboundEvent(type=InboundEventType.ITEM_UPDATED, payload=body, **common)]


def _issue_comment(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    if payload.get("action") == "deleted":
        return []
    issue = payload.get("issue", {})
    comment = payload.get("comment", {}) or {}
    key = _issue_key(payload, issue)
    common = _base(delivery_id, payload, key)
    body = comment.get("body") or ""
    command = parse_command(body)
    events = [
        InboundEvent(
            type=InboundEventType.ITEM_COMMENTED,
            payload={
                "body": body,
                "author": (comment.get("user") or {}).get("login"),
                "url": comment.get("html_url"),
            },
            **common,
        )
    ]
    if command:
        events.append(
            InboundEvent(
                type=InboundEventType.HUMAN_DECISION,
                payload={
                    "verb": command["verb"],
                    "argument": command["argument"],
                    "channel": "tracker",
                    "by": (comment.get("user") or {}).get("login"),
                },
                **common,
            )
        )
    return events


def _projects_v2_item(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    """Déplacement d'une carte : c'est une décision humaine autant qu'un changement d'état."""
    changes = payload.get("changes", {}) or {}
    field_value = changes.get("field_value", {}) or {}
    item = payload.get("projects_v2_item", {}) or {}
    content_key = item.get("content_node_id")
    key = payload.get("work_item_key") or content_key
    common = _base(delivery_id, payload, key)
    if field_value.get("field_name") in {"Status", "Statut"}:
        return [
            InboundEvent(
                type=InboundEventType.ITEM_MOVED,
                payload={
                    "field": field_value.get("field_name"),
                    "from_status": (field_value.get("from") or {}).get("name"),
                    "to_status": (field_value.get("to") or {}).get("name"),
                    "project_number": (payload.get("projects_v2_item", {}) or {}).get("project_node_id"),
                },
                **common,
            )
        ]
    return [InboundEvent(type=InboundEventType.ITEM_UPDATED, payload={"changes": changes}, **common)]


def _pull_request(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    pr = payload.get("pull_request", {}) or {}
    action = payload.get("action")
    key = _work_item_from_branch(pr.get("head", {}).get("ref", ""), payload)
    common = _base(delivery_id, payload, key)
    body = {
        "number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "sha": (pr.get("head") or {}).get("sha"),
        "base": (pr.get("base") or {}).get("ref"),
        "head": (pr.get("head") or {}).get("ref"),
        "draft": pr.get("draft"),
        "labels": [label.get("name") for label in pr.get("labels", [])],
        "merged": pr.get("merged", False),
    }
    if action == "opened":
        return [InboundEvent(type=InboundEventType.PR_OPENED, payload=body, **common)]
    if action == "synchronize":
        return [InboundEvent(type=InboundEventType.PR_SYNCHRONIZED, payload=body, **common)]
    if action == "closed":
        kind = InboundEventType.PR_MERGED if pr.get("merged") else InboundEventType.PR_CLOSED
        return [InboundEvent(type=kind, payload=body, **common)]
    return []


def _pull_request_review(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    review = payload.get("review", {}) or {}
    pr = payload.get("pull_request", {}) or {}
    key = _work_item_from_branch((pr.get("head") or {}).get("ref", ""), payload)
    common = _base(delivery_id, payload, key)
    return [
        InboundEvent(
            type=InboundEventType.PR_REVIEW_SUBMITTED,
            payload={
                "state": (review.get("state") or "").lower(),
                "reviewer": (review.get("user") or {}).get("login"),
                "body": review.get("body") or "",
                "pr_url": pr.get("html_url"),
                "number": pr.get("number"),
            },
            **common,
        )
    ]


def _check_suite(delivery_id: str, payload: dict[str, Any]) -> list[InboundEvent]:
    suite = payload.get("check_suite", payload.get("check_run", {})) or {}
    if payload.get("action") not in {"completed", None}:
        return []
    prs = suite.get("pull_requests", []) or []
    head_branch = suite.get("head_branch", "")
    key = _work_item_from_branch(head_branch, payload)
    common = _base(delivery_id, payload, key)
    return [
        InboundEvent(
            type=InboundEventType.CHECK_COMPLETED,
            payload={
                "conclusion": suite.get("conclusion"),
                "sha": suite.get("head_sha"),
                "branch": head_branch,
                "pr_numbers": [pr.get("number") for pr in prs],
                "url": suite.get("html_url"),
            },
            **common,
        )
    ]


BRANCH_KEY_RE = re.compile(r"choregos/(?P<number>\d+)")


def _work_item_from_branch(branch: str, payload: dict[str, Any]) -> str | None:
    """`choregos/123-slug` désigne le ticket #123 du dépôt."""
    match = BRANCH_KEY_RE.search(branch or "")
    if match is None:
        return None
    return f"{_repo_slug(payload)}#{match.group('number')}"


_HANDLERS = {
    "issues": _issues,
    "issue_comment": _issue_comment,
    "projects_v2_item": _projects_v2_item,
    "pull_request": _pull_request,
    "pull_request_review": _pull_request_review,
    "check_suite": _check_suite,
    "check_run": _check_suite,
}
