# SPDX-License-Identifier: Apache-2.0
"""The Jira Cloud TrackerAdapter: issues, transitions, comments, custom fields.

Jira has neither scoped labels like GitHub nor free columns: the DSL state becomes a workflow
**transition**, and the structured fields (cost, size, risk, run) are named custom fields,
resolved once and then cached.

Comments go out in Atlassian Document Format through the v3 API: the status table (§4.3) is
converted, otherwise Jira would display raw markdown.

Credentials: an API token with its account's email, as HTTP Basic. A classic token is sent to the
site itself; a *scoped* token (the only kind an Atlassian service account has) is refused there and
goes through the gateway, `api.atlassian.com/ex/jira/<cloud_id>` (`JiraGatewayClient`).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from choregos_contracts import InboundEvent, ProjectConfig, Risk, Size, StartRule
from choregos_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData

from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient
from .adf import markdown_to_adf, text_of_adf
from .fields import COST, FORMER_NAMES, RISK, RUN, SIZE, current_name
from .jira_events import parse_jira_event, person_of
from .ticket_fields import TRACKER_FIELD, jira_answers, person, tracker_block

STATUS_COMMENT_MARKER = "[choregos:status]"
GATEWAY = "https://api.atlassian.com"
AGENT_READY_LABEL = "agent-ready"


# Jira's three status categories have keys that are stable in every language; their names, on the
# other hand, are translated. See `JiraTracker._transition`.
_CANONICAL_CATEGORIES = {"to do": "new", "in progress": "indeterminate", "done": "done"}


class JiraGatewayClient(RestClient):
    """The REST client of a scoped token: every call goes through `api.atlassian.com/ex/jira/<cloud_id>`.

    Without a `cloud_id`, the first call reads it from the site's public `/_edge/tenant_info`, so the
    connector needs only the site, the email, the token and `auth: scoped`.
    """

    def __init__(self, site: str, *, cloud_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(f"{GATEWAY}/ex/jira/{cloud_id}" if cloud_id else site, **kwargs)
        self.site = site.rstrip("/")
        self.cloud_id = cloud_id or None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self.cloud_id is None:
            response = await self._client.get(f"{self.site}/_edge/tenant_info")
            found = response.json().get("cloudId") if response.status_code == 200 else None
            if not found:
                raise ConfigurationError(
                    f"cannot read the cloud id of {self.site} (/_edge/tenant_info answered "
                    f"{response.status_code}): set `cloud_id`"
                )
            self.cloud_id = str(found)
            self.base_url = f"{GATEWAY}/ex/jira/{self.cloud_id}"
        return await super().request(method, path, **kwargs)


class JiraTracker:
    """Jira Cloud (REST v3). `project_key` is the prefix of the keys (`BILL-42`)."""

    owns_items = True

    def __init__(
        self,
        client: RestClient,
        project_key: str,
        *,
        webhook_secret: str = "",
        field_names: dict[str, str] | None = None,
        agent_ready_label: str = AGENT_READY_LABEL,
        site_url: str | None = None,
        candidates_jql: str | None = None,
    ) -> None:
        if not project_key:
            raise ConfigurationError("`project_key` is required for the Jira tracker")
        self.client = client
        # Where a person opens an issue: the site, even when the API is called through the gateway.
        self.site_url = (site_url or getattr(client, "site", None) or client.base_url).rstrip("/")
        self.project_key = project_key.upper()
        self.webhook_secret = webhook_secret
        # The Jira-side names of the structured fields Choregos writes. A configuration from before
        # S22-21 designated them by their former logical name (French `Taille`): it counts as the
        # new one (`Size`).
        self.field_names = {
            COST: COST,
            SIZE: SIZE,
            RISK: RISK,
            RUN: RUN,
            **{current_name(logical): name for logical, name in (field_names or {}).items()},
        }
        self.agent_ready_label = agent_ready_label
        # The issues the poll takes on without any Jira-side setup (no webhook, no automation rule).
        self.candidates_jql = (candidates_jql or "").strip() or None
        self._fields: dict[str, str] | None = None

    # ───────────────────────── lecture ─────────────────────────

    async def fetch_item(self, key: str) -> WorkItemData:
        # `names`: the display name of each custom field, which keys the form's answers (S51-04).
        issue = await self.client.request(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": "*all", "expand": "renderedFields,names"}
        )
        fields = issue.get("fields", {}) or {}
        comments = ((fields.get("comment") or {}).get("comments")) or []
        labels = list(fields.get("labels") or [])
        tracker = tracker_block(
            kind="jira",
            key=str(issue.get("key") or key),
            reporter=person(fields.get("reporter")),
            creator=person(fields.get("creator")),
            labels=labels,
            answers=jira_answers(fields, issue.get("names") or {}),
        )
        return WorkItemData(
            key=key,
            title=fields.get("summary", ""),
            body=text_of_adf(fields.get("description")),
            url=f"{self.site_url}/browse/{key}",
            state=((fields.get("status") or {}).get("name")),
            labels=labels,
            item_type=((fields.get("issuetype") or {}).get("name")),
            size=_parse_enum(Size, fields, self._present_name(SIZE, fields)),
            risk=_parse_enum(Risk, fields, self._present_name(RISK, fields)),
            assignees=[a for a in [((fields.get("assignee") or {}).get("displayName"))] if a],
            author=person_of(fields.get("reporter")),
            reopened_at=_back_to_work_at(fields),
            fields={TRACKER_FIELD: tracker},
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
        """The open issues the poll takes on: those labelled `agent-ready` (S3-06), or, when the
        connector sets `candidates_jql`, those matching it, or those matching one of the project's
        start rules (S56-03), each rule a JQL clause; always within the project."""
        selection = (
            f"({self.candidates_jql})" if self.candidates_jql else f"labels = {_jql(self.agent_ready_label)}"
        )
        if project.start_rules:
            rules = [f"({_rule_jql(rule)})" for rule in project.start_rules]
            selection = "(" + " OR ".join([selection, *rules]) + ")"
        jql = (
            f'project = "{self.project_key}" AND {selection} AND statusCategory != Done ORDER BY updated DESC'
        )

        payload = await self.client.request(
            "GET", "/rest/api/3/search/jql", params={"jql": jql, "maxResults": 100, "fields": "key"}
        )
        return [str(issue["key"]) for issue in payload.get("issues", [])]

    # ───────────────────────── writing ─────────────────────────

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
        """The DSL state becomes a Jira transition; the label follows in parallel."""
        if state_mapping.status:
            await self._transition(key, state_mapping.status)
        if state_mapping.label or state_mapping.remove_labels:
            issue = await self.client.request("GET", f"/rest/api/3/issue/{key}", params={"fields": "labels"})
            current = list((issue.get("fields") or {}).get("labels") or [])
            keep = [name for name in current if name not in state_mapping.remove_labels]
            if state_mapping.label and state_mapping.label not in keep:
                keep.append(state_mapping.label)
            await self.client.request("PUT", f"/rest/api/3/issue/{key}", json={"fields": {"labels": keep}})

    async def close_item(self, key: str) -> None:
        """Nothing to do: in Jira, closing IS a status. The workflow's terminal state already carries it
        (`tracker: { status: Done }`, applied by `set_state`); a second transition here would look
        for one out of `Done`, which does not exist (S22-28)."""
        return

    async def _transition(self, key: str, status: str) -> None:
        """Jira does not let a status be *written*: the transition that leads to it must be found.

        Three ways to designate the target, in this order:

        1. the **exact name** of the destination status or of the transition: what a project
           writes in its own `workflow.yaml`;
        2. an explicit **category**, `category:new`, `category:indeterminate` or
           `category:done`: the three keys that Jira keeps identical in every language;
        3. one of the three **canonical English names** (`To Do`, `In Progress`, `Done`),
           translated to its category.

        The third case exists because Jira **translates the status names** into the language of the
        site: on a French instance, "In Progress" is translated, and so is "Done".
        Choregos' templates write the English names; without this fallback, no ticket moved on a
        French-speaking Jira, which is what the live test against `meltingcode.atlassian.net`
        showed. The category fallback only applies when **a single** transition leads there: two
        "in progress" statuses (one in progress, one in review) are not told apart by chance, they raise an
        error that names them.
        """
        payload = await self.client.request("GET", f"/rest/api/3/issue/{key}/transitions")
        transitions = payload.get("transitions", [])
        wanted = status.casefold()

        for transition in transitions:
            target = ((transition.get("to") or {}).get("name") or "").casefold()
            if target == wanted or (transition.get("name") or "").casefold() == wanted:
                await self._apply(key, transition)
                return

        category = (
            wanted.removeprefix("category:")
            if wanted.startswith("category:")
            else _CANONICAL_CATEGORIES.get(wanted)
        )
        if category:
            matches = [
                t
                for t in transitions
                if (((t.get("to") or {}).get("statusCategory") or {}).get("key")) == category
            ]
            if len(matches) == 1:
                await self._apply(key, matches[0])
                return
            if len(matches) > 1:
                names = ", ".join((t.get("to") or {}).get("name", "?") for t in matches)
                raise ConfigurationError(
                    f'"{status}" designates the category `{category}`, and several statuses of {key} '
                    f"lead there ({names}): name the one you want in `workflow.yaml`"
                )

        available = ", ".join((t.get("to") or {}).get("name", t.get("name", "?")) for t in transitions)
        raise ConfigurationError(
            f'no Jira transition leads to "{status}" from the current state of {key} '
            f"(available: {available or 'none'})"
        )

    async def _apply(self, key: str, transition: dict[str, Any]) -> None:
        await self.client.request(
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": transition["id"]}},
        )

    async def upsert_status_comment(
        self, key: str, markdown: str, marker: str = STATUS_COMMENT_MARKER
    ) -> None:
        """A single status comment, rewritten at every stage (§4.3)."""
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
        """Jira has real issue links: they are used rather than a comment."""
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
        """Writes the custom fields, ignoring those the Jira project does not have."""
        mapping = await self._field_ids()
        payload: dict[str, Any] = {}
        for name, value in fields.items():
            field_id = mapping.get(self._present_name(current_name(name), mapping, casefold=True).casefold())
            if field_id is None:
                continue  # a missing field is not a failure: Choregos writes what it can
            payload[field_id] = str(value) if not isinstance(value, int | float) else value
        if payload:
            await self.client.request("PUT", f"/rest/api/3/issue/{key}", json={"fields": payload})

    def _present_name(self, logical: str, present: Mapping[str, Any], *, casefold: bool = False) -> str:
        """The Jira name of the field: the configured one, otherwise (when it is not configured and
        the Jira project was set up before S22-21) its former French name (`Taille`)."""  # lang: fr-ok
        name = self.field_names.get(logical, logical)
        former = FORMER_NAMES.get(name) if name == logical else None
        fold = str.casefold if casefold else str
        if former is not None and fold(name) not in present and fold(former) in present:
            return former
        return name

    async def _field_ids(self) -> dict[str, str]:
        if self._fields is None:
            fields = await self.client.request("GET", "/rest/api/3/field")
            self._fields = {
                str(field.get("name", "")).casefold(): str(field["id"]) for field in fields if field.get("id")
            }
        return self._fields

    # ───────────────────────── test ─────────────────────────

    async def test(self) -> dict[str, Any]:
        """Can the account read the project? The connector's **Test** button: it proves the email,
        the token, its scopes and the account's place in the project in one call."""
        try:
            project = await self.client.request("GET", f"/rest/api/3/project/{self.project_key}")
        except UpstreamError as error:
            return {"ok": False, "reason": _refusal(error.status_code, self.project_key)}
        cloud_id = getattr(self.client, "cloud_id", None)
        return {
            "ok": True,
            "project": project.get("name") or self.project_key,
            **({"cloud_id": cloud_id} if cloud_id else {}),
        }

    # ───────────────────────── webhooks ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        import json

        return parse_jira_event(headers.get("X-Atlassian-Webhook-Identifier", ""), json.loads(body or b"{}"))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Jira Cloud does not sign its webhooks: a shared secret is required in a header."""
        import hmac

        provided = headers.get("X-Choregos-Secret", headers.get("x-choregos-secret", ""))
        return bool(self.webhook_secret) and hmac.compare_digest(self.webhook_secret, provided or "")


def _refusal(status_code: int | None, project_key: str) -> str:
    """What a refused test means, in the words of the setup procedure."""
    if status_code == 401:
        return (
            "401: the email or the token is refused. The email must be the token's account; "
            "a scoped token (a service account's) needs `auth: scoped`"
        )
    if status_code == 403:
        return (
            "403: the token lacks a scope (`read:jira-work`, `write:jira-work`) "
            "or the account lacks a permission on the project"
        )
    if status_code == 404:
        return f"404: no project {project_key}, or the account cannot browse it: add it to the project"
    return f"{status_code}: Jira refused to read the project {project_key}"


def _back_to_work_at(fields: dict[str, Any]) -> datetime | None:
    """When the issue entered the To Do or In Progress category it is in now (C1): Jira's
    `statuscategorychangedate`, read only while the issue is not in a Done status."""
    category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
    changed = fields.get("statuscategorychangedate")
    if category == "done" or not isinstance(changed, str) or not changed:
        return None
    try:
        return datetime.fromisoformat(changed)
    except ValueError:
        return None


def _parse_enum(enum: Any, fields: dict[str, Any], name: str) -> Any:
    """Reads a size or a risk from a custom field, whatever its shape."""
    raw = fields.get(name)
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name")
    if not raw:
        return None
    try:
        return enum(str(raw).upper() if enum is Size else str(raw).lower())
    except ValueError:
        return None


def _jql(value: str) -> str:
    """A JQL string literal: quotes and backslashes escaped, so a label cannot end the clause."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rule_jql(rule: StartRule) -> str:
    """A start rule (S56-03) as a JQL clause: every condition, joined by AND."""
    parts: list[str] = []
    if rule.labels_any:
        parts.append(f"labels in ({', '.join(_jql(label) for label in rule.labels_any)})")
    parts.extend(f"labels = {_jql(label)}" for label in rule.labels_all)
    if rule.item_type:
        parts.append(f"issuetype = {_jql(rule.item_type)}")
    if rule.states:
        parts.append(f"status in ({', '.join(_jql(state) for state in rule.states)})")
    return " AND ".join(parts)
