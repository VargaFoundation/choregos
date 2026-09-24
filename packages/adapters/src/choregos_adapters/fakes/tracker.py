"""Tracker en mémoire : scriptable, déterministe, sans réseau."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from choregos_contracts import InboundEvent, InboundEventType, ProjectConfig, Risk, Size
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData, utcnow


class FakeTracker:
    """Tracker de test. `seed()` crée des tickets ; `move()` simule un déplacement de carte."""

    owns_items = True

    def __init__(self, project_slug: str = "demo") -> None:
        self.project_slug = project_slug
        self.items: dict[str, WorkItemData] = {}
        self.links: list[tuple[str, str, str]] = []
        self.calls: list[tuple[str, Any]] = []
        self._next_number = 1
        self.webhook_secret = "fake-secret"

    # ───────────────────────── scripting ─────────────────────────

    def seed(
        self,
        title: str,
        body: str = "",
        *,
        labels: list[str] | None = None,
        size: Size | None = None,
        risk: Risk | None = None,
    ) -> str:
        key = f"{self.project_slug}#{self._next_number}"
        self._next_number += 1
        self.items[key] = WorkItemData(
            key=key,
            title=title,
            body=body,
            url=f"https://fake.tracker/{key}",
            labels=labels or [],
            size=size,
            risk=risk,
            state="Todo",
            created_at=utcnow(),
        )
        return key

    def move(self, key: str, to_status: str, actor: str = "humain") -> InboundEvent:
        item = self.items[key]
        previous = item.state
        item.state = to_status
        return InboundEvent(
            type=InboundEventType.ITEM_MOVED,
            source="fake",
            delivery_id=f"move-{key}-{to_status}",
            project_slug=self.project_slug,
            work_item_key=key,
            actor=actor,
            payload={"from_status": previous, "to_status": to_status},
        )

    def status_comment(self, key: str, marker: str = "<!-- choregos:status -->") -> str | None:
        for comment in reversed(self.items[key].comments):
            if comment.marker == marker:
                return comment.body
        return None

    # ───────────────────────── TrackerAdapter ─────────────────────────

    async def fetch_item(self, key: str) -> WorkItemData:
        self.calls.append(("fetch_item", key))
        if key not in self.items:
            raise KeyError(f"ticket inconnu : {key}")
        return self.items[key].model_copy(deep=True)

    async def create_item(self, data: NewItem) -> str:
        key = self.seed(data.title, data.body, labels=list(data.labels))
        self.items[key].fields.update(data.fields)
        self.items[key].assignees = list(data.assignees)
        self.calls.append(("create_item", key))
        return key

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        self.calls.append(("set_state", (key, state_mapping.status, state_mapping.label)))
        item = self.items[key]
        if state_mapping.status:
            item.state = state_mapping.status
        if state_mapping.label:
            item.labels = [label for label in item.labels if not label.startswith("choregos:")]
            item.labels.append(state_mapping.label)
        for label in state_mapping.remove_labels:
            if label in item.labels:
                item.labels.remove(label)

    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None:
        """Un seul commentaire de statut par ticket, réécrit à chaque étape."""
        self.calls.append(("upsert_status_comment", key))
        item = self.items[key]
        for comment in item.comments:
            if comment.marker == marker:
                comment.body = markdown
                comment.ts = utcnow()
                return
        item.comments.append(
            Comment(id=f"c{len(item.comments) + 1}", author="choregos-bot", body=markdown, marker=marker)
        )

    async def comment(self, key: str, markdown: str) -> str:
        item = self.items[key]
        comment_id = f"c{len(item.comments) + 1}"
        item.comments.append(Comment(id=comment_id, author="choregos-bot", body=markdown))
        self.calls.append(("comment", key))
        return comment_id

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        self.links.append((a, b, relation))

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        self.calls.append(("set_fields", (key, dict(fields))))
        self.items[key].fields.update(fields)

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        return [key for key, item in self.items.items() if "agent-ready" in item.labels]

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        payload = json.loads(body.decode("utf-8"))
        return [InboundEvent.model_validate(payload)] if payload else []

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        return headers.get("X-Fake-Secret") == self.webhook_secret
