"""TrackerAdapter GitHub : Issues (REST) + Projects v2 (GraphQL).

Le board est la vue humaine : Choregos y écrit l'état, le coût, la taille, le risque
et le lien vers le run. Les champs sont créés à la demande s'ils n'existent pas.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from choregos_contracts import InboundEvent, ProjectConfig, Risk, Size
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData

from ..errors import ConfigurationError
from ..github.client import GitHubClient
from .github_events import parse_github_event

STATUS_COMMENT_MARKER = "<!-- choregos:status -->"

PROJECT_FIELDS_QUERY = """
query($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField { id name options { id name } }
        }
      }
    }
  }
}
"""

ITEM_BY_CONTENT_QUERY = """
query($projectId: ID!, $first: Int!, $after: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id content { ... on Issue { number } } }
      }
    }
  }
}
"""

ADD_ITEM_MUTATION = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) { item { id } }
}
"""

UPDATE_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: $value}
  ) {
    projectV2Item { id }
  }
}
"""

CREATE_OPTION_MUTATION = """
mutation($fieldId: ID!, $name: String!, $color: ProjectV2SingleSelectFieldOptionColor!) {
  updateProjectV2Field(
    input: {fieldId: $fieldId, singleSelectOptions: [{name: $name, color: $color, description: ""}]}
  ) {
    projectV2Field {
      ... on ProjectV2SingleSelectField { id options { id name } }
    }
  }
}
"""


class GitHubTracker:
    """Issues + Projects v2. `repo` est `owner/name` ; `project_number` est le board."""

    def __init__(
        self,
        client: GitHubClient,
        repo: str,
        *,
        project_number: int | None = None,
        org: str | None = None,
        webhook_secret: str = "",
        label_prefix: str = "choregos:",
    ) -> None:
        if "/" not in repo:
            raise ConfigurationError(f"`repo` doit valoir owner/name (reçu : {repo})")
        self.client = client
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.project_number = project_number
        self.org = org or self.owner
        self.webhook_secret = webhook_secret
        self.label_prefix = label_prefix
        self._project_cache: dict[str, Any] | None = None

    # ───────────────────────── lecture ─────────────────────────

    def _number(self, key: str) -> int:
        return int(key.rsplit("#", 1)[-1])

    async def fetch_item(self, key: str) -> WorkItemData:
        number = self._number(key)
        issue = await self.client.request("GET", f"/repos/{self.repo}/issues/{number}", repo=self.repo)
        comments = await self.client.paginate(f"/repos/{self.repo}/issues/{number}/comments", repo=self.repo)
        return WorkItemData(
            key=key,
            title=issue.get("title", ""),
            body=issue.get("body") or "",
            url=issue.get("html_url"),
            state=issue.get("state"),
            labels=[label["name"] for label in issue.get("labels", [])],
            size=_parse_size(issue.get("labels", [])),
            risk=_parse_risk(issue.get("labels", [])),
            assignees=[a["login"] for a in issue.get("assignees", [])],
            author=(issue.get("user") or {}).get("login"),
            comments=[
                Comment(
                    id=str(c.get("id")),
                    author=(c.get("user") or {}).get("login", ""),
                    body=c.get("body") or "",
                    marker=STATUS_COMMENT_MARKER if STATUS_COMMENT_MARKER in (c.get("body") or "") else None,
                )
                for c in comments
            ],
        )

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        """Polling de secours : les issues `agent-ready` ouvertes (S3-06)."""
        issues = await self.client.paginate(
            f"/repos/{self.repo}/issues",
            repo=self.repo,
            params={"state": "open", "labels": "agent-ready", "sort": "updated"},
        )
        return [f"{self.repo}#{issue['number']}" for issue in issues if "pull_request" not in issue]

    # ───────────────────────── écriture ─────────────────────────

    async def create_item(self, data: NewItem) -> str:
        issue = await self.client.request(
            "POST",
            f"/repos/{data.repo or self.repo}/issues",
            repo=data.repo or self.repo,
            json={
                "title": data.title,
                "body": data.body,
                "labels": data.labels,
                "assignees": data.assignees,
            },
        )
        return f"{data.repo or self.repo}#{issue['number']}"

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        """Label scopé côté issue, colonne *Status* côté board."""
        number = self._number(key)
        if state_mapping.label:
            issue = await self.client.request("GET", f"/repos/{self.repo}/issues/{number}", repo=self.repo)
            current = [label["name"] for label in issue.get("labels", [])]
            keep = [name for name in current if not name.startswith(self.label_prefix)]
            keep = [name for name in keep if name not in state_mapping.remove_labels]
            await self.client.request(
                "PUT",
                f"/repos/{self.repo}/issues/{number}/labels",
                repo=self.repo,
                json={"labels": [*keep, state_mapping.label]},
            )
        if state_mapping.status and self.project_number:
            await self._set_board_field(number, "Status", state_mapping.status)

    async def upsert_status_comment(
        self, key: str, markdown: str, marker: str = STATUS_COMMENT_MARKER
    ) -> None:
        """Un unique commentaire de suivi, réécrit à chaque étape (§4.3)."""
        number = self._number(key)
        body = markdown if marker in markdown else f"{marker}\n{markdown}"
        comments = await self.client.paginate(f"/repos/{self.repo}/issues/{number}/comments", repo=self.repo)
        for comment in comments:
            if marker in (comment.get("body") or ""):
                await self.client.request(
                    "PATCH",
                    f"/repos/{self.repo}/issues/comments/{comment['id']}",
                    repo=self.repo,
                    json={"body": body},
                )
                return
        await self.client.request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", repo=self.repo, json={"body": body}
        )

    async def comment(self, key: str, markdown: str) -> str:
        number = self._number(key)
        created = await self.client.request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", repo=self.repo, json={"body": markdown}
        )
        return str(created.get("id"))

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        wording = {
            "origin": f"Origine : {b}",
            "duplicate": f"Doublon de {b}",
            "blocks": f"Bloque {b}",
        }[relation]
        await self.comment(a, f"<!-- choregos:link:{relation} -->\n{wording}")

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        """Champs structurés du board : *Coût (€)*, *Taille*, *Risque*, *Run*."""
        if not self.project_number:
            return
        number = self._number(key)
        for name, value in fields.items():
            await self._set_board_field(number, name, value)

    # ───────────────────────── Projects v2 ─────────────────────────

    async def _project(self) -> dict[str, Any]:
        if self._project_cache is None:
            data = await self.client.graphql(
                PROJECT_FIELDS_QUERY, {"org": self.org, "number": self.project_number}, repo=self.repo
            )
            project = (data.get("organization") or {}).get("projectV2")
            if not project:
                raise ConfigurationError(
                    f"board Projects v2 #{self.project_number} introuvable pour {self.org}"
                )
            self._project_cache = project
        return self._project_cache

    async def _item_id_for(self, issue_number: int) -> str | None:
        project = await self._project()
        after: str | None = None
        while True:
            data = await self.client.graphql(
                ITEM_BY_CONTENT_QUERY,
                {"projectId": project["id"], "first": 100, "after": after},
                repo=self.repo,
            )
            items = (data.get("node") or {}).get("items") or {}
            for node in items.get("nodes", []):
                content = node.get("content") or {}
                if content.get("number") == issue_number:
                    return str(node["id"])
            page = items.get("pageInfo", {})
            if not page.get("hasNextPage"):
                return None
            after = page.get("endCursor")

    async def _ensure_item(self, issue_number: int) -> str:
        existing = await self._item_id_for(issue_number)
        if existing:
            return existing
        issue = await self.client.request("GET", f"/repos/{self.repo}/issues/{issue_number}", repo=self.repo)
        project = await self._project()
        data = await self.client.graphql(
            ADD_ITEM_MUTATION, {"projectId": project["id"], "contentId": issue["node_id"]}, repo=self.repo
        )
        return str(data["addProjectV2ItemById"]["item"]["id"])

    async def _set_board_field(self, issue_number: int, field_name: str, value: Any) -> None:
        project = await self._project()
        field = next((f for f in project["fields"]["nodes"] if f.get("name") == field_name), None)
        if field is None:
            return  # le champ n'existe pas sur ce board : on n'invente rien
        item_id = await self._ensure_item(issue_number)
        payload: dict[str, Any]
        if field.get("dataType") == "SINGLE_SELECT" or "options" in field:
            option = next((o for o in field.get("options", []) if o["name"] == str(value)), None)
            if option is None:
                option = await self._create_option(field, str(value))
            payload = {"singleSelectOptionId": option["id"]}
        elif field.get("dataType") == "NUMBER":
            payload = {"number": float(value)}
        else:
            payload = {"text": str(value)}
        await self.client.graphql(
            UPDATE_FIELD_MUTATION,
            {"projectId": project["id"], "itemId": item_id, "fieldId": field["id"], "value": payload},
            repo=self.repo,
        )

    async def _create_option(self, field: dict[str, Any], name: str) -> dict[str, Any]:
        """Crée l'option *Status* manquante : les états du DSL pilotent le board, pas l'inverse."""
        data = await self.client.graphql(
            CREATE_OPTION_MUTATION, {"fieldId": field["id"], "name": name, "color": "GRAY"}, repo=self.repo
        )
        updated = data["updateProjectV2Field"]["projectV2Field"]
        field["options"] = updated.get("options", [])
        self._project_cache = None
        option = next((o for o in field["options"] if o["name"] == name), None)
        return option or {"id": ""}

    # ───────────────────────── webhooks ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        import json

        event = headers.get("X-GitHub-Event", headers.get("x-github-event", ""))
        delivery = headers.get("X-GitHub-Delivery", headers.get("x-github-delivery", ""))
        return parse_github_event(event, delivery, json.loads(body or b"{}"))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        import hashlib
        import hmac

        signature = headers.get("X-Hub-Signature-256", headers.get("x-hub-signature-256", ""))
        if not self.webhook_secret or not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def ensure_labels(self, labels: dict[str, str]) -> None:
        """Provisioning : crée les labels manquants (nom → couleur)."""
        existing = {
            label["name"]
            for label in await self.client.paginate(f"/repos/{self.repo}/labels", repo=self.repo)
        }
        for name, color in labels.items():
            if name in existing:
                continue
            await self.client.request(
                "POST", f"/repos/{self.repo}/labels", repo=self.repo, json={"name": name, "color": color}
            )

    async def test(self) -> dict[str, Any]:
        repo = await self.client.request("GET", f"/repos/{self.repo}", repo=self.repo)
        checks: dict[str, Any] = {
            "ok": True,
            "repo": repo.get("full_name"),
            "permissions": repo.get("permissions"),
        }
        if self.project_number:
            try:
                project = await self._project()
                checks["project"] = project["id"]
            except Exception as exc:
                checks["ok"] = False
                checks["project_error"] = str(exc)[:200]
        return checks


def _parse_size(labels: list[dict[str, Any]]) -> Size | None:
    for label in labels:
        name = label.get("name", "")
        if name.startswith("size:"):
            value = name.split(":", 1)[1].upper()
            if value in {"S", "M", "L", "XL"}:
                return Size(value)
    return None


def _parse_risk(labels: list[dict[str, Any]]) -> Risk | None:
    for label in labels:
        name = label.get("name", "")
        if name.startswith("risk:"):
            value = name.split(":", 1)[1].lower()
            if value in {"low", "medium", "high"}:
                return Risk(value)
    return None
