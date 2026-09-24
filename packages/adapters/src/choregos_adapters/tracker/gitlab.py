"""TrackerAdapter GitLab : issues, labels scopés, notes, champs dans la description.

GitLab ressemble davantage à GitHub qu'à Jira : des issues numérotées, des labels, des
notes. Deux différences comptent : les labels **scopés** (`choregos::implement`) sont
exclusifs entre eux côté GitLab, ce qui reflète exactement un état de workflow ; et il n'y
a pas de champs de board, donc le coût, la taille et le risque vivent dans un bloc de
métadonnées en fin de description, réécrit à chaque fois.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

from choregos_contracts import InboundEvent, ProjectConfig, Risk, Size
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData

from ..errors import ConfigurationError
from ..http import RestClient
from .gitlab_events import parse_gitlab_event

STATUS_COMMENT_MARKER = "<!-- choregos:status -->"
METADATA_START = "<!-- choregos:fields"
METADATA_END = "-->"
METADATA_RE = re.compile(re.escape(METADATA_START) + r"(?P<json>.*?)" + re.escape(METADATA_END), re.S)


class GitLabTracker:
    """GitLab Issues (API v4). `project` est le chemin complet (`groupe/sous-groupe/projet`)."""

    owns_items = True

    def __init__(
        self,
        client: RestClient,
        project: str,
        *,
        webhook_secret: str = "",
        label_prefix: str = "choregos::",
    ) -> None:
        if not project:
            raise ConfigurationError("`project` est obligatoire pour le tracker GitLab")
        self.client = client
        self.project = project
        self.encoded = quote(project, safe="")
        self.webhook_secret = webhook_secret
        self.label_prefix = label_prefix

    def _iid(self, key: str) -> int:
        return int(key.rsplit("#", 1)[-1])

    def _key(self, iid: int) -> str:
        return f"{self.project}#{iid}"

    # ───────────────────────── lecture ─────────────────────────

    async def fetch_item(self, key: str) -> WorkItemData:
        iid = self._iid(key)
        issue = await self.client.request("GET", f"/api/v4/projects/{self.encoded}/issues/{iid}")
        notes = await self.client.paginate(f"/api/v4/projects/{self.encoded}/issues/{iid}/notes")
        description = issue.get("description") or ""
        metadata = _read_metadata(description)
        return WorkItemData(
            key=key,
            title=issue.get("title", ""),
            body=_strip_metadata(description),
            url=issue.get("web_url"),
            state=issue.get("state"),
            labels=list(issue.get("labels") or []),
            size=_as_enum(Size, metadata.get("Taille"), upper=True),
            risk=_as_enum(Risk, metadata.get("Risque")),
            assignees=[a.get("username", "") for a in issue.get("assignees", [])],
            author=(issue.get("author") or {}).get("username"),
            comments=[
                Comment(
                    id=str(note.get("id")),
                    author=(note.get("author") or {}).get("username", ""),
                    body=note.get("body") or "",
                    marker=STATUS_COMMENT_MARKER
                    if STATUS_COMMENT_MARKER in (note.get("body") or "")
                    else None,
                )
                for note in notes
                if not note.get("system")
            ],
        )

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        """Polling de secours (S3-06) : les issues ouvertes étiquetées `agent-ready`."""
        issues = await self.client.paginate(
            f"/api/v4/projects/{self.encoded}/issues",
            params={"state": "opened", "labels": "agent-ready", "order_by": "updated_at"},
        )
        return [self._key(int(issue["iid"])) for issue in issues]

    # ───────────────────────── écriture ─────────────────────────

    async def create_item(self, data: NewItem) -> str:
        issue = await self.client.request(
            "POST",
            f"/api/v4/projects/{self.encoded}/issues",
            json={
                "title": data.title,
                "description": data.body,
                "labels": ",".join(data.labels),
            },
        )
        return self._key(int(issue["iid"]))

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        """Un label scopé `choregos::<état>` : GitLab garantit lui-même l'exclusivité."""
        iid = self._iid(key)
        payload: dict[str, Any] = {}
        if state_mapping.label:
            payload["add_labels"] = state_mapping.label
        removals = [label for label in state_mapping.remove_labels if label]
        if removals:
            payload["remove_labels"] = ",".join(removals)
        if payload:
            await self.client.request("PUT", f"/api/v4/projects/{self.encoded}/issues/{iid}", json=payload)

    async def upsert_status_comment(
        self, key: str, markdown: str, marker: str = STATUS_COMMENT_MARKER
    ) -> None:
        """Une unique note de suivi, réécrite à chaque étape (§4.3)."""
        iid = self._iid(key)
        body = markdown if marker in markdown else f"{marker}\n{markdown}"
        notes = await self.client.paginate(f"/api/v4/projects/{self.encoded}/issues/{iid}/notes")
        for note in notes:
            if marker in (note.get("body") or ""):
                await self.client.request(
                    "PUT",
                    f"/api/v4/projects/{self.encoded}/issues/{iid}/notes/{note['id']}",
                    json={"body": body},
                )
                return
        await self.client.request(
            "POST", f"/api/v4/projects/{self.encoded}/issues/{iid}/notes", json={"body": body}
        )

    async def comment(self, key: str, markdown: str) -> str:
        iid = self._iid(key)
        note = await self.client.request(
            "POST", f"/api/v4/projects/{self.encoded}/issues/{iid}/notes", json={"body": markdown}
        )
        return str(note.get("id", ""))

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        """GitLab a des liens d'issues typés (`relates_to`, `blocks`, `is_blocked_by`)."""
        link_type = {"origin": "relates_to", "duplicate": "relates_to", "blocks": "blocks"}[relation]
        await self.client.request(
            "POST",
            f"/api/v4/projects/{self.encoded}/issues/{self._iid(a)}/links",
            json={
                "target_project_id": self.encoded,
                "target_issue_iid": self._iid(b),
                "link_type": link_type,
            },
        )

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        """Pas de champs de board côté GitLab : un bloc de métadonnées en fin de description."""
        iid = self._iid(key)
        issue = await self.client.request("GET", f"/api/v4/projects/{self.encoded}/issues/{iid}")
        description = issue.get("description") or ""
        merged = {**_read_metadata(description), **{k: str(v) for k, v in fields.items()}}
        body = _strip_metadata(description).rstrip()
        payload = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        await self.client.request(
            "PUT",
            f"/api/v4/projects/{self.encoded}/issues/{iid}",
            json={"description": f"{body}\n\n{METADATA_START} {payload} {METADATA_END}\n"},
        )

    # ───────────────────────── webhooks ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        event = headers.get("X-Gitlab-Event", headers.get("x-gitlab-event", ""))
        delivery = headers.get("X-Gitlab-Event-UUID", headers.get("x-gitlab-event-uuid", ""))
        return parse_gitlab_event(event, delivery, json.loads(body or b"{}"))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """GitLab envoie un jeton partagé, pas une signature HMAC."""
        import hmac

        token = headers.get("X-Gitlab-Token", headers.get("x-gitlab-token", ""))
        return bool(self.webhook_secret) and hmac.compare_digest(self.webhook_secret, token or "")


def _read_metadata(description: str) -> dict[str, str]:
    match = METADATA_RE.search(description or "")
    if match is None:
        return {}
    try:
        parsed = json.loads(match.group("json").strip())
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def _strip_metadata(description: str) -> str:
    return METADATA_RE.sub("", description or "").rstrip()


def _as_enum(enum: Any, raw: Any, *, upper: bool = False) -> Any:
    if not raw:
        return None
    try:
        return enum(str(raw).upper() if upper else str(raw).lower())
    except ValueError:
        return None
