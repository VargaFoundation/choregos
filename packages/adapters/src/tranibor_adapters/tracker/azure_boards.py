# SPDX-License-Identifier: Apache-2.0
"""The Azure DevOps Boards TrackerAdapter: work items, states, comments, fields, tags, links (ADR 0209).

Azure DevOps Services (`https://dev.azure.com/<organisation>`) and Azure DevOps Server 2022 (the
collection URL, `https://<server>/<virtual directory>/<collection>`) speak the same REST API 7.1; the
connector's `base_url` is the only difference. The Jira tracker is the model, and the differences are
the platform's:

* a state is **written**, not transitioned: the DSL state becomes `System.State`, validated first
  against the states of the item's type (exact name, or the state *category*, which every process
  keeps whatever the states are called);
* labels are **tags**, one `;`-separated string;
* the structured fields (cost, size, risk, run) are custom fields, resolved by display name to their
  reference name (`Custom.Size`) once and cached;
* descriptions and comments are HTML, and updates are JSON Patch.

Credentials: a **personal access token** or, on the Services only, a **Microsoft Entra service
principal**; the client that carries them is the one of `tranibor_adapters.azure_devops`, shared with the
Repos and Pipelines connectors.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from tranibor_contracts import InboundEvent, ProjectConfig, Risk, Size, StartRule
from tranibor_core.domain import Comment, NewItem, TrackerStateMapping, WorkItemData

from ..azure_devops import (  # noqa: F401 - the client and the secret reader, re-exported for importers of this module
    JSON_PATCH,
    AzureDevOpsClient,
    EntraClientCredentials,
    shared_secret_of,
)
from ..errors import ConfigurationError, UpstreamError
from ..http import RestClient
from .azure_boards_events import identity_text, parse_azure_boards_event, tags_of
from .azure_html import html_to_text, markdown_to_html
from .fields import COST, FORMER_NAMES, RISK, RUN, SIZE, current_name
from .ticket_fields import TRACKER_FIELD, answers, person, tracker_block

STATUS_COMMENT_MARKER = "[choregos:status]"
AGENT_READY_LABEL = "agent-ready"
#: The comments endpoints exist only as a preview in 7.1.
COMMENTS_API_VERSION = "7.1-preview.4"
_MAX_COMMENT_PAGES = 10

#: The state categories of Azure Boards, by the Jira-style words the other trackers use for them and by
#: the canonical English names a template writes (`To Do`, `In Progress`, `Done`).
_CATEGORY_WORDS = {"new": "Proposed", "indeterminate": "InProgress", "done": "Completed"}
_CANONICAL_CATEGORIES = {"to do": "Proposed", "in progress": "InProgress", "done": "Completed"}
_CATEGORIES = {c.casefold(): c for c in ("Proposed", "InProgress", "Resolved", "Completed", "Removed")}
_LINK_TYPES = {
    "origin": "System.LinkTypes.Related",
    "duplicate": "System.LinkTypes.Duplicate-Forward",
    "blocks": "System.LinkTypes.Dependency-Forward",
}


class AzureBoardsTracker:
    """Azure DevOps Boards. `project` is the team project; `area_path` narrows it to a team's area."""

    owns_items = True

    def __init__(
        self,
        client: RestClient,
        project: str,
        *,
        area_path: str | None = None,
        webhook_secret: str = "",
        field_names: dict[str, str] | None = None,
        agent_ready_label: str = AGENT_READY_LABEL,
        site_url: str | None = None,
        candidates_wiql: str | None = None,
    ) -> None:
        if not project:
            raise ConfigurationError("`project` is required for the Azure Boards tracker")
        self.client = client
        self.site_url = (site_url or client.base_url).rstrip("/")
        self.project = project
        self.area_path = (area_path or "").strip() or None
        self.webhook_secret = webhook_secret
        self.field_names = {
            COST: COST,
            SIZE: SIZE,
            RISK: RISK,
            RUN: RUN,
            **{current_name(logical): name for logical, name in (field_names or {}).items()},
        }
        self.agent_ready_label = agent_ready_label
        # The items the poll takes on without any Azure-side setup (no service hook).
        self.candidates_wiql = (candidates_wiql or "").strip() or None
        self._fields: dict[str, str] | None = None
        self._names: dict[str, str] | None = None
        self._states: dict[str, list[dict[str, Any]]] = {}

    @property
    def _scope(self) -> str:
        return f"/{quote(self.project, safe='')}/_apis"

    # ───────────────────────── reading ─────────────────────────

    async def fetch_item(self, key: str) -> WorkItemData:
        item = await self.client.request(
            "GET", f"{self._scope}/wit/workitems/{key}", params={"$expand": "all"}
        )
        fields: dict[str, Any] = item.get("fields") or {}
        by_name = await self._field_ids()
        ref = self._present_ref(SIZE, by_name), self._present_ref(RISK, by_name)
        labels = tags_of(fields.get("System.Tags"))
        reporter = _identity(fields.get("System.CreatedBy"))
        tracker = tracker_block(
            kind="azure-boards",
            key=str(item.get("id") or key),
            reporter=person(reporter),
            creator=person(reporter),
            labels=labels,
            answers=await self._answers(fields),
        )
        assignee = _identity(fields.get("System.AssignedTo")).get("displayName")
        return WorkItemData(
            key=key,
            title=str(fields.get("System.Title") or ""),
            body=html_to_text(fields.get("System.Description")),
            url=self.item_url(key),
            state=fields.get("System.State"),
            labels=labels,
            item_type=fields.get("System.WorkItemType"),
            size=_parse_enum(Size, fields.get(ref[0] or "")),
            risk=_parse_enum(Risk, fields.get(ref[1] or "")),
            assignees=[a for a in [assignee] if a],
            author=identity_text(fields.get("System.CreatedBy")),
            created_at=_when(fields.get("System.CreatedDate")),
            updated_at=_when(fields.get("System.ChangedDate")),
            closed_at=_when(fields.get("Microsoft.VSTS.Common.ClosedDate")),
            reopened_at=await self._back_to_work_at(fields),
            fields={TRACKER_FIELD: tracker},
            comments=[
                Comment(
                    id=str(comment.get("id")),
                    author=str((comment.get("createdBy") or {}).get("displayName", "")),
                    body=html_to_text(comment.get("text")),
                    marker=STATUS_COMMENT_MARKER
                    if STATUS_COMMENT_MARKER in html_to_text(comment.get("text"))
                    else None,
                )
                for comment in await self._comments(key)
            ],
        )

    def item_url(self, key: str) -> str:
        return f"{self.site_url}/{quote(self.project, safe='')}/_workitems/edit/{key}"

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        """The open work items the poll takes on: those tagged `agent-ready`, or, when the connector sets
        `candidates_wiql`, those matching it, or those matching one of the project's start rules; always
        within the team project (and the area path, if set), never Completed or Removed."""
        selection = (
            f"({self.candidates_wiql})"
            if self.candidates_wiql
            else f"[System.Tags] CONTAINS {_wiql(self.agent_ready_label)}"
        )
        if project.start_rules:
            rules = [f"({_rule_wiql(rule)})" for rule in project.start_rules]
            selection = "(" + " OR ".join([selection, *rules]) + ")"
        scope = f"[System.TeamProject] = {_wiql(self.project)}"
        if self.area_path:
            scope += f" AND [System.AreaPath] UNDER {_wiql(self.area_path)}"
        wiql = (
            f"SELECT [System.Id] FROM WorkItems WHERE {scope} AND {selection} "  # noqa: S608 - WIQL, quoted by `_wiql`
            "AND [System.StateCategory] <> 'Completed' AND [System.StateCategory] <> 'Removed' "
            "ORDER BY [System.ChangedDate] DESC"
        )
        payload = await self.client.request(
            "POST", f"{self._scope}/wit/wiql", params={"$top": 100}, json={"query": wiql}
        )
        return [str(found["id"]) for found in payload.get("workItems", []) if found.get("id") is not None]

    async def _comments(self, key: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(_MAX_COMMENT_PAGES):
            params: dict[str, Any] = {"api-version": COMMENTS_API_VERSION, "$top": 200}
            if token:
                params["continuationToken"] = token
            page = await self.client.request(
                "GET", f"{self._scope}/wit/workItems/{key}/comments", params=params
            )
            found.extend(page.get("comments") or [])
            token = page.get("continuationToken")
            if not token:
                break
        return found

    async def _answers(self, fields: Mapping[str, Any]) -> dict[str, str]:
        """Every non-empty custom field of the item (`Custom.*`), by its display name, sorted."""
        names = await self._reference_names()
        custom = [(names.get(ref, ref), value) for ref, value in fields.items() if ref.startswith("Custom.")]
        return answers(sorted(custom, key=lambda pair: pair[0].casefold()))

    async def _back_to_work_at(self, fields: Mapping[str, Any]) -> datetime | None:
        """When the item entered the state it is in now, read only while that state is not in the
        Completed or Removed category (C1): `StateChangeDate`. Best effort: a category that cannot be read
        leaves it empty rather than failing the read."""
        changed = _when(fields.get("Microsoft.VSTS.Common.StateChangeDate"))
        kind, state = fields.get("System.WorkItemType"), fields.get("System.State")
        if changed is None or not kind or not state:
            return None
        try:
            states = await self._states_of(str(kind))
        except UpstreamError:
            return None
        category = next((s.get("category") for s in states if _same(s.get("name"), state)), None)
        return None if category in {"Completed", "Removed", None} else changed

    # ───────────────────────── writing ─────────────────────────

    async def create_item(self, data: NewItem) -> str:
        kind = str(data.fields.get("issue_type", "Task"))
        operations = [
            _add("System.Title", data.title),
            _add("System.Description", markdown_to_html(data.body)),
        ]
        if data.labels:
            operations.append(_add("System.Tags", "; ".join(data.labels)))
        if self.area_path:
            operations.append(_add("System.AreaPath", self.area_path))
        if data.assignees:
            operations.append(_add("System.AssignedTo", data.assignees[0]))
        created = await self.client.request(
            "POST",
            f"{self._scope}/wit/workitems/${quote(kind, safe='')}",
            json=operations,
            headers=JSON_PATCH,
        )
        return str(created["id"])

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        """The DSL state is written to `System.State`; the tags follow in parallel."""
        if state_mapping.status:
            await self._move(key, state_mapping.status)
        if state_mapping.label or state_mapping.remove_labels:
            item = await self.client.request(
                "GET", f"{self._scope}/wit/workitems/{key}", params={"fields": "System.Tags"}
            )
            current = tags_of((item.get("fields") or {}).get("System.Tags"))
            keep = [tag for tag in current if tag not in state_mapping.remove_labels]
            if state_mapping.label and state_mapping.label not in keep:
                keep.append(state_mapping.label)
            if keep != current:
                await self._patch(key, [_add("System.Tags", "; ".join(keep))])

    async def close_item(self, key: str) -> None:
        """Nothing to do: in Azure Boards, closing IS a state. The workflow's terminal state already
        carries it (`tracker: { status: Done }`, applied by `set_state`)."""
        return

    async def _move(self, key: str, status: str) -> None:
        """Writes the state, after finding which of the item type's states `status` designates.

        Three ways to designate it, in this order, as for Jira:

        1. the **exact name** of a state of the item's type (processes differ: `Closed` in Agile,
           `Done` in Scrum and Basic);
        2. an explicit **category**, `category:Proposed`, `category:InProgress`, `category:Resolved`,
           `category:Completed`, `category:Removed`, or the Jira words `new`, `indeterminate`, `done`;
        3. one of the three **canonical English names** (`To Do`, `In Progress`, `Done`), by category.

        A category that several states share (Scrum's `Approved` and `Committed` are both in progress)
        is not picked by chance: the error names them. A state the item is already in is not written
        again, so a replayed activity changes nothing (ADR 0008)."""
        item = await self.client.request(
            "GET",
            f"{self._scope}/wit/workitems/{key}",
            params={"fields": "System.State,System.WorkItemType"},
        )
        fields = item.get("fields") or {}
        states = await self._states_of(str(fields.get("System.WorkItemType") or ""))
        target = self._resolve_state(key, status, states)
        if _same(fields.get("System.State"), target):
            return
        await self._patch(key, [_add("System.State", target)])

    @staticmethod
    def _resolve_state(key: str, status: str, states: list[dict[str, Any]]) -> str:
        for state in states:
            if _same(state.get("name"), status):
                return str(state["name"])
        wanted = status.strip().casefold()
        category = (
            _CATEGORIES.get(wanted.removeprefix("category:"))
            or _CATEGORY_WORDS.get(wanted.removeprefix("category:"))
            if wanted.startswith("category:")
            else _CATEGORY_WORDS.get(wanted) or _CANONICAL_CATEGORIES.get(wanted)
        )
        if category:
            matches = [s for s in states if s.get("category") == category]
            if len(matches) == 1:
                return str(matches[0]["name"])
            if len(matches) > 1:
                names = ", ".join(str(s.get("name")) for s in matches)
                raise ConfigurationError(
                    f'"{status}" designates the category `{category}`, and several states of {key} '
                    f"are in it ({names}): name the one you want in `workflow.yaml`"
                )
        available = ", ".join(str(s.get("name")) for s in states)
        raise ConfigurationError(
            f'no state of the type of {key} is "{status}" (available: {available or "none"})'
        )

    async def _states_of(self, item_type: str) -> list[dict[str, Any]]:
        if item_type not in self._states:
            payload = await self.client.request(
                "GET", f"{self._scope}/wit/workitemtypes/{quote(item_type, safe='')}/states"
            )
            self._states[item_type] = [s for s in payload.get("value", []) if s.get("name")]
        return self._states[item_type]

    async def _patch(self, key: str, operations: list[dict[str, Any]]) -> None:
        await self.client.request(
            "PATCH", f"{self._scope}/wit/workitems/{key}", json=operations, headers=JSON_PATCH
        )

    async def upsert_status_comment(
        self, key: str, markdown: str, marker: str = STATUS_COMMENT_MARKER
    ) -> None:
        """A single status comment, rewritten at every stage (§4.3)."""
        body = markdown if marker in markdown else f"{marker}\n{markdown}"
        text = markdown_to_html(body)
        params = {"api-version": COMMENTS_API_VERSION}
        for comment in await self._comments(key):
            if marker in html_to_text(comment.get("text")):
                await self.client.request(
                    "PATCH",
                    f"{self._scope}/wit/workItems/{key}/comments/{comment['id']}",
                    params=params,
                    json={"text": text},
                )
                return
        await self.client.request(
            "POST", f"{self._scope}/wit/workItems/{key}/comments", params=params, json={"text": text}
        )

    async def comment(self, key: str, markdown: str) -> str:
        created = await self.client.request(
            "POST",
            f"{self._scope}/wit/workItems/{key}/comments",
            params={"api-version": COMMENTS_API_VERSION},
            json={"text": markdown_to_html(markdown)},
        )
        return str(created.get("id", ""))

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        """Azure Boards has real work item links: they are used rather than a comment. An existing link
        is not an error (a replayed activity)."""
        rel = _LINK_TYPES[relation]
        target = f"{self.site_url}/_apis/wit/workItems/{b}"
        try:
            await self._patch(
                a, [{"op": "add", "path": "/relations/-", "value": {"rel": rel, "url": target}}]
            )
        except UpstreamError as error:
            if error.status_code == 400 and "already exist" in str(error).casefold():
                return
            raise

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        """Writes the custom fields, ignoring those the project does not have."""
        by_name = await self._field_ids()
        operations: list[dict[str, Any]] = []
        for name, value in fields.items():
            ref = self._present_ref(current_name(name), by_name)
            if ref is None:
                continue  # a missing field is not a failure: Tranibor writes what it can
            operations.append(_add(ref, value if isinstance(value, int | float) else str(value)))
        if operations:
            await self._patch(key, operations)

    def _present_ref(self, logical: str, by_name: Mapping[str, str]) -> str | None:
        """The reference name of the field Tranibor writes as `logical`: the configured display name
        (or a reference name given as is), otherwise, for a project set up before S22-21, the former one."""
        name = self.field_names.get(logical, logical)
        for candidate in (name, FORMER_NAMES.get(logical) if name == logical else None):
            if candidate is None:
                continue
            found = by_name.get(candidate.casefold())
            if found is not None:
                return found
        return None

    async def _load_fields(self) -> None:
        payload = await self.client.request("GET", f"{self._scope}/wit/fields")
        listed = [f for f in payload.get("value", []) if f.get("referenceName")]
        self._fields = {}
        self._names = {}
        for field in listed:
            ref = str(field["referenceName"])
            self._names[ref] = str(field.get("name") or ref)
            self._fields[str(field.get("name") or ref).casefold()] = ref
            self._fields.setdefault(ref.casefold(), ref)

    async def _field_ids(self) -> dict[str, str]:
        if self._fields is None:
            await self._load_fields()
        return self._fields or {}

    async def _reference_names(self) -> dict[str, str]:
        if self._names is None:
            await self._load_fields()
        return self._names or {}

    # ───────────────────────── test ─────────────────────────

    async def test(self) -> dict[str, Any]:
        """Can the credential read the project? The connector's **Test** button: it proves the token (or
        the service principal), its scopes and its place in the project in one call."""
        try:
            project = await self.client.request(
                "GET", f"{self.client.base_url}/_apis/projects/{quote(self.project, safe='')}"
            )
        except UpstreamError as error:
            return {"ok": False, "reason": _refusal(error.status_code, self.project)}
        return {"ok": True, "project": project.get("name") or self.project}

    # ───────────────────────── webhooks ─────────────────────────

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        return parse_azure_boards_event("", json.loads(body or b"{}"))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """A service hook does not sign its deliveries: it carries a secret, as the password of the
        basic authentication the subscription sets, or in an `X-Choregos-Secret` header."""
        return bool(self.webhook_secret) and hmac.compare_digest(
            self.webhook_secret, shared_secret_of(headers)
        )


def _refusal(status_code: int | None, project: str) -> str:
    """What a refused test means, in the words of the setup procedure."""
    if status_code in {401, 203}:
        return (
            "401: the token is refused or has expired, or the service principal is not a member of the "
            "organisation (Azure DevOps answers a sign-in page, 203, for a bad token on some servers)"
        )
    if status_code == 403:
        return (
            "403: the credential lacks a scope (Work Items: Read & write, Project and Team: Read) "
            "or a permission on the project"
        )
    if status_code == 404:
        return f"404: no project {project}, or the credential cannot see it: add the account to the project"
    return f"{status_code}: Azure DevOps refused to read the project {project}"


def _add(path: str, value: Any) -> dict[str, Any]:
    return {"op": "add", "path": f"/fields/{path}", "value": value}


def _same(left: Any, right: Any) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _identity(raw: Any) -> dict[str, Any]:
    """An Azure identity in the shape `ticket_fields.person` reads;
    the unique name is an e-mail on the Services and `DOMAIN\\user` on a Server, so only an e-mail is kept."""
    if isinstance(raw, str):
        name, _, rest = raw.partition("<")
        email = rest.rstrip("> ").strip()
        return {"displayName": name.strip() or None, "emailAddress": email if "@" in email else None}
    found = raw if isinstance(raw, dict) else {}
    unique = str(found.get("uniqueName") or "")
    return {
        "accountId": found.get("id") or found.get("descriptor"),
        "displayName": found.get("displayName"),
        "emailAddress": unique if "@" in unique else None,
    }


_FRACTION = re.compile(r"(\.\d{6})\d+")


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(_FRACTION.sub(r"\1", value))
    except ValueError:
        return None


def _parse_enum(enum: Any, raw: Any) -> Any:
    """Reads a size or a risk from a custom field, whatever its shape."""
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name") or raw.get("displayName")
    if not raw:
        return None
    try:
        return enum(str(raw).upper() if enum is Size else str(raw).lower())
    except ValueError:
        return None


def _wiql(value: str) -> str:
    """A WIQL string literal: single quotes doubled, so a tag cannot end the clause."""
    return "'" + value.replace("'", "''") + "'"


def _rule_wiql(rule: StartRule) -> str:
    """A start rule (S56-03) as a WIQL clause: every condition, joined by AND."""
    parts: list[str] = []
    if rule.labels_any:
        parts.append(
            "(" + " OR ".join(f"[System.Tags] CONTAINS {_wiql(label)}" for label in rule.labels_any) + ")"
        )
    parts.extend(f"[System.Tags] CONTAINS {_wiql(label)}" for label in rule.labels_all)
    if rule.item_type:
        parts.append(f"[System.WorkItemType] = {_wiql(rule.item_type)}")
    if rule.states:
        parts.append(f"[System.State] IN ({', '.join(_wiql(state) for state in rule.states)})")
    return " AND ".join(parts)
