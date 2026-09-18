"""TrackerAdapter Jira Cloud : issues, transitions, commentaires, champs personnalisés.

Jira n'a ni labels scopés comme GitHub ni colonnes libres : l'état du DSL devient une
**transition** de workflow, et les champs structurés (coût, taille, risque, run) sont des
champs personnalisés nommés, résolus une fois puis mis en cache.

Les commentaires partent en Atlassian Document Format via l'API v3 : le tableau de suivi
(§4.3) est converti, sinon Jira afficherait du markdown brut.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from choregos_contracts import InboundEvent, ProjectConfig, Risk, Size
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData

from ..errors import ConfigurationError
from ..http import RestClient
from .adf import markdown_to_adf, text_of_adf
from .jira_events import parse_jira_event

STATUS_COMMENT_MARKER = "[choregos:status]"
AGENT_READY_LABEL = "agent-ready"


class JiraTracker:
    """Jira Cloud (REST v3). `project_key` est le préfixe des clés (`BILL-42`)."""

    def __init__(
        self,
        client: RestClient,
        project_key: str,
        *,
        webhook_secret: str = "",
        field_names: dict[str, str] | None = None,
        agent_ready_label: str = AGENT_READY_LABEL,
    ) -> None:
        if not project_key:
            raise ConfigurationError("`project_key` est obligatoire pour le tracker Jira")
        self.client = client
        self.project_key = project_key.upper()
        self.webhook_secret = webhook_secret
        # Noms côté Jira des champs structurés que Choregos écrit.
        self.field_names = {
            "Coût (€)": "Coût (€)",
            "Taille": "Taille",
            "Risque": "Risque",
            "Run": "Run",
            **(field_names or {}),
        }
        self.agent_ready_label = agent_ready_label
        self._fields: dict[str, str] | None = None

    # ───────────────────────── lecture ─────────────────────────

    async def fetch_item(self, key: str) -> WorkItemData:
        issue = await self.client.request(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": "*all", "expand": "renderedFields"}
        )
        fields = issue.get("fields", {}) or {}
        comments = ((fields.get("comment") or {}).get("comments")) or []
        labels = list(fields.get("labels") or [])
        return WorkItemData(
            key=key,
            title=fields.get("summary", ""),
            body=text_of_adf(fields.get("description")),
            url=f"{self.client.base_url}/browse/{key}",
            state=((fields.get("status") or {}).get("name")),
            labels=labels,
            size=_parse_enum(Size, fields, self.field_names["Taille"]),
            risk=_parse_enum(Risk, fields, self.field_names["Risque"]),
            assignees=[a for a in [((fields.get("assignee") or {}).get("displayName"))] if a],
            author=((fields.get("reporter") or {}).get("displayName")),
            comments=[
                Comment(
                    id=str(comment.get("id")),
                    author=((comment.get("author") or {}).get("displayName", "")),
                    body=text_of_adf(comment.get("body")),
                    marker=STATUS_COMMENT_MARKER
                    if STATUS_COMMENT_MARKER in text_of_adf(comment.get("body"))
                    else None,
                )
                for comment in comments
            ],
        )

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        """Polling de secours (S3-06) : les issues ouvertes étiquetées `agent-ready`."""
        jql = (
            f'project = "{self.project_key}" AND labels = "{self.agent_ready_label}" '
            "AND statusCategory != Done ORDER BY updated DESC"
        )
        payload = await self.client.request(
            "GET", "/rest/api/3/search/jql", params={"jql": jql, "maxResults": 100, "fields": "key"}
        )
        return [str(issue["key"]) for issue in payload.get("issues", [])]

    # ───────────────────────── écriture ─────────────────────────

    async def create_item(self, data: NewItem) -> str:
        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": data.title,
                "description": markdown_to_adf(data.body),
                "issuetype": {"name": data.fields.get("issue_type", "Task")},
                "labels": list(data.labels),
            }
        }
        if data.assignees:
            payload["fields"]["assignee"] = {"accountId": data.assignees[0]}
        issue = await self.client.request("POST", "/rest/api/3/issue", json=payload)
        return str(issue["key"])

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        """L'état du DSL devient une transition Jira ; le label suit en parallèle."""
        if state_mapping.status:
            await self._transition(key, state_mapping.status)
        if state_mapping.label or state_mapping.remove_labels:
            issue = await self.client.request("GET", f"/rest/api/3/issue/{key}", params={"fields": "labels"})
            current = list((issue.get("fields") or {}).get("labels") or [])
            keep = [name for name in current if name not in state_mapping.remove_labels]
            if state_mapping.label and state_mapping.label not in keep:
                keep.append(state_mapping.label)
            await self.client.request("PUT", f"/rest/api/3/issue/{key}", json={"fields": {"labels": keep}})

    async def _transition(self, key: str, status: str) -> None:
        """Jira ne se laisse pas *écrire* un statut : il faut trouver la transition qui y mène."""
        payload = await self.client.request("GET", f"/rest/api/3/issue/{key}/transitions")
        wanted = status.casefold()
        for transition in payload.get("transitions", []):
            target = ((transition.get("to") or {}).get("name") or "").casefold()
            if target == wanted or (transition.get("name") or "").casefold() == wanted:
                await self.client.request(
                    "POST",
                    f"/rest/api/3/issue/{key}/transitions",
                    json={"transition": {"id": transition["id"]}},
                )
                return
        available = ", ".join(
            (t.get("to") or {}).get("name", t.get("name", "?")) for t in payload.get("transitions", [])
        )
        raise ConfigurationError(
            f"aucune transition Jira ne mène à « {status} » depuis l'état courant de {key} "
            f"(disponibles : {available or 'aucune'})"
        )

    async def upsert_status_comment(
        self, key: str, markdown: str, marker: str = STATUS_COMMENT_MARKER
    ) -> None:
        """Un unique commentaire de suivi, réécrit à chaque étape (§4.3)."""
        body = markdown if marker in markdown else f"{marker}\n{markdown}"
        document = markdown_to_adf(body)
        comments = await self.client.request(
            "GET", f"/rest/api/3/issue/{key}/comment", params={"maxResults": 100}
        )
        for comment in comments.get("comments", []):
            if marker in text_of_adf(comment.get("body")):
                await self.client.request(
                    "PUT",
                    f"/rest/api/3/issue/{key}/comment/{comment['id']}",
                    json={"body": document},
                )
                return
        await self.client.request("POST", f"/rest/api/3/issue/{key}/comment", json={"body": document})

    async def comment(self, key: str, markdown: str) -> str:
        created = await self.client.request(
            "POST", f"/rest/api/3/issue/{key}/comment", json={"body": markdown_to_adf(markdown)}
        )
        return str(created.get("id", ""))

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        """Jira a de vrais liens d'issues : on les utilise plutôt qu'un commentaire."""
        link_type = {"origin": "Relates", "duplicate": "Duplicate", "blocks": "Blocks"}[relation]
        await self.client.request(
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": b},
                "outwardIssue": {"key": a},
            },
        )

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        """Écrit les champs personnalisés, en ignorant ceux que le projet Jira n'a pas."""
        mapping = await self._field_ids()
        payload: dict[str, Any] = {}
        for name, value in fields.items():
            field_id = mapping.get(self.field_names.get(name, name).casefold())
            if field_id is None:
                continue  # un champ absent n'est pas une panne : Choregos écrit ce qu'il peut
            payload[field_id] = str(value) if not isinstance(value, int | float) else value
        if payload:
            await self.client.request("PUT", f"/rest/api/3/issue/{key}", json={"fields": payload})

    async def _field_ids(self) -> dict[str, str]:
        if self._fields is None:
            fields = await self.client.request("GET", "/rest/api/3/field")
            self._fields = {
                str(field.get("name", "")).casefold(): str(field["id"]) for field in fields if field.get("id")
            }
        return self._fields

    # ───────────────────────── webhooks ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        import json

        return parse_jira_event(headers.get("X-Atlassian-Webhook-Identifier", ""), json.loads(body or b"{}"))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Jira Cloud ne signe pas ses webhooks : on exige un secret partagé en en-tête."""
        import hmac

        provided = headers.get("X-Choregos-Secret", headers.get("x-choregos-secret", ""))
        return bool(self.webhook_secret) and hmac.compare_digest(self.webhook_secret, provided or "")


def _parse_enum(enum: Any, fields: dict[str, Any], name: str) -> Any:
    """Lit une taille ou un risque dans un champ personnalisé, quelle que soit sa forme."""
    raw = fields.get(name)
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name")
    if not raw:
        return None
    try:
        return enum(str(raw).upper() if enum is Size else str(raw).lower())
    except ValueError:
        return None
