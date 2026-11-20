"""The Azure DevOps Boards tracker: what Tranibor sends on the wire, and what it understands in return.

The tests speak the REST API 7.1 and the service-hook payloads as Microsoft documents them, through a
scriptable in-memory server (`FakeAzureBoardsServer`) and a few hand-written transports. They prove
the shape of the requests and the tracker's behaviour; not that a real organisation accepts them.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from tranibor_adapters.errors import ConfigurationError, UpstreamError
from tranibor_adapters.fakes.azure_boards import FakeAzureBoardsServer
from tranibor_adapters.tracker.azure_boards import (
    STATUS_COMMENT_MARKER,
    AzureBoardsTracker,
    AzureDevOpsClient,
    EntraClientCredentials,
    shared_secret_of,
)
from tranibor_adapters.tracker.azure_boards_events import parse_azure_boards_event
from tranibor_adapters.tracker.azure_html import html_to_text, markdown_to_html
from tranibor_contracts import InboundEventType, ProjectConfig, RepoConfig, Risk, Size, StartRule
from tranibor_core.domain import NewItem, TrackerStateMapping

BASE = "https://dev.azure.com/northwind"


def build(server: FakeAzureBoardsServer, **options: Any) -> AzureBoardsTracker:
    client = AzureDevOpsClient(
        BASE,
        pat=server.pat,
        client=httpx.AsyncClient(transport=server.transport()),
        service="azure-boards",
    )
    return AzureBoardsTracker(client, "Billing", webhook_secret="s3cret", **options)


def config(**fields: Any) -> ProjectConfig:
    return ProjectConfig(slug="bill", org="northwind", repo=RepoConfig(url="https://x/y.git"), **fields)


# ───────────────────────────── reading ─────────────────────────────


async def test_reading_a_work_item() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed(
        "Credit notes are not deducted",
        **{
            "System.Description": "<div>When an order has a <b>credit note</b>, the total ignores it.</div>",
            "System.Tags": "agent-ready; billing",
            "System.State": "Active",
            "System.CreatedBy": {
                "displayName": "Augustin",
                "uniqueName": "augustin@northwind.dev",
                "id": "u1",
            },
            "System.AssignedTo": {"displayName": "Marie", "uniqueName": "marie@northwind.dev"},
            "Custom.Size": "m",
            "Custom.Risk": "Low",
            "Custom.Customer": "Contoso",
            "System.CreatedDate": "2026-11-01T10:00:00.1234567Z",
        },
    )

    item = await build(server).fetch_item(str(number))

    assert item.title == "Credit notes are not deducted"
    assert "credit note" in item.body and "<" not in item.body
    assert item.state == "Active" and item.item_type == "Task"
    assert item.labels == ["agent-ready", "billing"]
    assert item.size is Size.M and item.risk is Risk.LOW
    assert item.assignees == ["Marie"]
    assert item.author == "Augustin <augustin@northwind.dev>"
    assert item.url == f"{BASE}/Billing/_workitems/edit/{number}"
    assert item.created_at is not None and item.created_at.year == 2026
    tracker = item.fields["tracker"]
    assert tracker["kind"] == "azure-boards"
    assert tracker["reporter"]["email"] == "augustin@northwind.dev"
    assert tracker["answers"] == {"Customer": "Contoso", "Risk": "Low", "Size": "m"}
    assert tracker["labels"] == ["agent-ready", "billing"]


async def test_the_comments_are_read_with_their_marker() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    tracker = build(server)
    await tracker.comment(str(number), "hello")
    await tracker.upsert_status_comment(str(number), "### Status\n\nnew")

    item = await tracker.fetch_item(str(number))

    assert [c.marker for c in item.comments] == [None, STATUS_COMMENT_MARKER]
    assert item.comments[0].body == "hello" and item.comments[0].author == "Tranibor"


async def test_the_candidates_go_through_a_wiql_query() -> None:
    server = FakeAzureBoardsServer()
    ready = server.seed("ready", tags="agent-ready; x")
    server.seed("not tagged", tags="x")
    server.seed("done", state="Closed", tags="agent-ready")
    server.seed("removed", state="Removed", tags="agent-ready")

    found = await build(server, area_path="Billing\\Team A").list_candidates(config())

    assert found == [str(ready)]
    query = server.queries[0]
    assert "[System.TeamProject] = 'Billing'" in query
    assert "[System.AreaPath] UNDER 'Billing\\Team A'" in query
    assert "[System.Tags] CONTAINS 'agent-ready'" in query
    assert "<> 'Completed'" in query and "<> 'Removed'" in query


async def test_start_rules_and_a_quote_in_a_tag_cannot_end_the_clause() -> None:
    server = FakeAzureBoardsServer()
    rules = [
        StartRule(item_type="Bug", states=["New"], labels_any=["pay'roll", "hr"]),
        StartRule(labels_all=["a", "b"]),
    ]
    await build(server, candidates_wiql="[System.Parent] = 1").list_candidates(config(start_rules=rules))
    query = server.queries[0]
    assert "([System.Parent] = 1)" in query
    assert "([System.Tags] CONTAINS 'pay''roll' OR [System.Tags] CONTAINS 'hr')" in query
    assert "[System.WorkItemType] = 'Bug'" in query and "[System.State] IN ('New')" in query
    assert "[System.Tags] CONTAINS 'a' AND [System.Tags] CONTAINS 'b'" in query


async def test_reopened_at_is_read_only_outside_the_completed_category() -> None:
    server = FakeAzureBoardsServer()
    back = server.seed(
        "back", state="Active", **{"Microsoft.VSTS.Common.StateChangeDate": "2026-11-05T08:00:00Z"}
    )
    ended = server.seed(
        "ended", state="Closed", **{"Microsoft.VSTS.Common.StateChangeDate": "2026-11-05T08:00:00Z"}
    )
    tracker = build(server)
    assert (await tracker.fetch_item(str(back))).reopened_at is not None
    assert (await tracker.fetch_item(str(ended))).reopened_at is None


# ───────────────────────────── writing ─────────────────────────────


async def test_a_dsl_state_is_written_after_checking_the_types_states() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    await build(server).set_state(str(number), TrackerStateMapping(status="Closed"))
    assert server.items[number]["fields"]["System.State"] == "Closed"


async def test_the_canonical_names_and_the_categories_find_the_processs_states() -> None:
    server = FakeAzureBoardsServer()
    number = str(server.seed("t"))
    tracker = build(server)
    for asked, expected in [
        ("In Progress", "Active"),
        ("Done", "Closed"),
        ("category:Proposed", "New"),
        ("category:done", "Closed"),
        ("category:Resolved", "Resolved"),
        ("active", "Active"),
    ]:
        await tracker.set_state(number, TrackerStateMapping(status=asked))
        assert server.items[int(number)]["fields"]["System.State"] == expected, asked


async def test_an_already_reached_state_is_not_written_again() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t", state="Active")
    await build(server).set_state(str(number), TrackerStateMapping(status="Active"))
    assert not [call for call in server.calls if call[0] == "PATCH"], "a replayed activity changes nothing"


async def test_an_unknown_state_is_a_readable_error() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    with pytest.raises(ConfigurationError) as error:
        await build(server).set_state(str(number), TrackerStateMapping(status="Deployed"))
    assert "Deployed" in str(error.value) and "Active" in str(error.value)


async def test_two_states_of_one_category_are_not_told_apart_by_chance() -> None:
    server = FakeAzureBoardsServer()
    server.states["Task"] = [
        {"name": "New", "category": "Proposed"},
        {"name": "Approved", "category": "InProgress"},
        {"name": "Committed", "category": "InProgress"},
        {"name": "Done", "category": "Completed"},
    ]
    number = server.seed("t")
    with pytest.raises(ConfigurationError) as error:
        await build(server).set_state(str(number), TrackerStateMapping(status="In Progress"))
    assert "Approved" in str(error.value) and "Committed" in str(error.value)


async def test_labels_are_tags_added_and_removed_in_one_string() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t", tags="agent-ready; stage:plan; billing")
    await build(server).set_state(
        str(number), TrackerStateMapping(label="stage:review", remove_labels=["agent-ready", "stage:plan"])
    )
    assert server.items[number]["fields"]["System.Tags"] == "billing; stage:review"


async def test_the_status_comment_is_rewritten_not_duplicated_and_goes_out_as_html() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    tracker = build(server)
    markdown = "### Tranibor\n\n| Step | Cost |\n| --- | --- |\n| implement | 0.42 EUR |"
    await tracker.upsert_status_comment(str(number), markdown)
    await tracker.upsert_status_comment(str(number), "### Tranibor\n\nnewer")

    thread = server.comments[number]
    assert len(thread) == 1, "no second comment"
    assert "<h3>Tranibor</h3>" in thread[0]["text"] and "newer" in html_to_text(thread[0]["text"])
    assert STATUS_COMMENT_MARKER in html_to_text(thread[0]["text"])
    await tracker.upsert_status_comment(str(number), markdown)
    assert "<table>" in thread[0]["text"], "the table is not sent as raw markdown"


async def test_fields_absent_from_the_project_are_ignored_and_the_former_names_count() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    server.field_catalogue = [
        {"name": "Coût (€)", "referenceName": "Custom.Cout"},  # lang: fr-ok (former name)
        {"name": "Taille", "referenceName": "Custom.Taille"},  # lang: fr-ok (former name)
        {"name": "Risk", "referenceName": "Custom.Risk"},
    ]
    await build(server).set_fields(
        str(number), {"Cost (€)": 1.25, "Size": "M", "Risk": "low", "Run": "https://x/1"}
    )
    fields = server.items[number]["fields"]
    assert fields["Custom.Cout"] == 1.25 and fields["Custom.Taille"] == "M" and fields["Custom.Risk"] == "low"
    assert "Custom.Run" not in fields


async def test_the_structured_fields_are_written_by_their_reference_name() -> None:
    server = FakeAzureBoardsServer()
    number = server.seed("t")
    await build(server).set_fields(str(number), {"Cost (€)": 0.5, "Size": "L", "Run": "https://x/run/1"})
    fields = server.items[number]["fields"]
    assert (fields["Custom.Cost"], fields["Custom.Size"], fields["Custom.Run"]) == (
        0.5,
        "L",
        "https://x/run/1",
    )


async def test_a_configured_field_name_is_used() -> None:
    server = FakeAzureBoardsServer()
    server.field_catalogue.append({"name": "T-shirt", "referenceName": "Custom.Tshirt"})
    number = server.seed("t")
    await build(server, field_names={"Size": "T-shirt"}).set_fields(str(number), {"Size": "S"})
    assert server.items[number]["fields"]["Custom.Tshirt"] == "S"


async def test_creating_a_work_item_and_linking_it_twice() -> None:
    server = FakeAzureBoardsServer()
    origin = server.seed("origin")
    tracker = build(server, area_path="Billing\\Team A")
    server.field_catalogue.append({"name": "Area Path", "referenceName": "System.AreaPath"})
    server.field_catalogue.append({"name": "Tags", "referenceName": "System.Tags"})
    server.field_catalogue.append({"name": "Description", "referenceName": "System.Description"})

    key = await tracker.create_item(
        NewItem(
            title="Connection leak",
            body="Found in **review**",
            labels=["source:choregos"],
            fields={"issue_type": "Bug"},
        )
    )
    created = server.items[int(key)]
    assert created["fields"]["System.Title"] == "Connection leak"
    assert created["fields"]["System.WorkItemType"] == "Bug"
    assert "<strong>review</strong>" in created["fields"]["System.Description"]
    assert created["fields"]["System.Tags"] == "source:choregos"
    assert created["fields"]["System.AreaPath"] == "Billing\\Team A"

    await tracker.link(key, str(origin), "origin")
    await tracker.link(key, str(origin), "origin")  # a replay: the existing link is not an error
    assert len(created["relations"]) == 1
    assert created["relations"][0]["url"] == f"{BASE}/_apis/wit/workItems/{origin}"
    await tracker.link(key, str(origin), "blocks")
    assert created["relations"][1]["rel"] == "System.LinkTypes.Dependency-Forward"


async def test_closing_is_a_no_op() -> None:
    server = FakeAzureBoardsServer()
    await build(server).close_item("1")
    assert server.calls == []


async def test_an_azure_failure_is_named() -> None:
    server = FakeAzureBoardsServer()
    with pytest.raises(UpstreamError) as error:
        await build(server).fetch_item("999")
    assert "azure-boards" in str(error.value) and error.value.status_code == 404


# ───────────────────────────── credentials ─────────────────────────────


async def test_the_pat_goes_as_basic_with_an_empty_user_name() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        seen["version"] = request.url.params.get("api-version", "")
        return httpx.Response(200, json={"name": "Billing"})

    client = AzureDevOpsClient(
        BASE, pat="tok", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert (await AzureBoardsTracker(client, "Billing").test())["ok"] is True
    assert base64.b64decode(seen["authorization"].removeprefix("Basic ")) == b":tok"
    assert seen["version"] == "7.1"


async def test_a_service_principal_gets_a_token_for_the_azure_devops_resource_once() -> None:
    server = FakeAzureBoardsServer(entra_secret="shh")
    entra = EntraClientCredentials("tenant-1", "client-1", "shh")
    client = AzureDevOpsClient(BASE, entra=entra, client=httpx.AsyncClient(transport=server.transport()))
    number = server.seed("t")
    tracker = AzureBoardsTracker(client, "Billing")

    await tracker.fetch_item(str(number))
    await tracker.fetch_item(str(number))

    assert len(server.token_requests) == 1, "the token is cached until shortly before it expires"
    asked = server.token_requests[0]
    assert asked["grant_type"] == "client_credentials" and asked["client_id"] == "client-1"
    assert asked["scope"] == "499b84ac-1321-427f-aa17-267ca6975798/.default"
    assert any("/tenant-1/oauth2/v2.0/token" in path for _, path in server.calls)


async def test_a_refused_service_principal_is_named() -> None:
    server = FakeAzureBoardsServer(entra_secret="shh")
    entra = EntraClientCredentials("tenant-1", "client-1", "wrong")
    client = AzureDevOpsClient(BASE, entra=entra, client=httpx.AsyncClient(transport=server.transport()))
    with pytest.raises(UpstreamError, match="Entra token endpoint refused"):
        await AzureBoardsTracker(client, "Billing").fetch_item("1")


def test_one_credential_and_only_one() -> None:
    with pytest.raises(ConfigurationError):
        AzureDevOpsClient(BASE)
    with pytest.raises(ConfigurationError):
        AzureDevOpsClient(BASE, pat="x", entra=EntraClientCredentials("t", "c", "s"))


async def test_the_connector_test_says_what_a_refusal_means() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="no")

    client = AzureDevOpsClient(
        BASE, pat="x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    answer = await AzureBoardsTracker(client, "Billing").test()
    assert answer["ok"] is False and answer["reason"].startswith("401")


# ───────────────────────────── webhooks ─────────────────────────────


def _basic(password: str, user: str = "hook") -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_the_shared_secret_protects_the_webhook_by_header_or_basic_auth() -> None:
    tracker = build(FakeAzureBoardsServer())
    assert tracker.verify_webhook({"X-Choregos-Secret": "s3cret"}, b"{}") is True
    assert tracker.verify_webhook({"x-choregos-secret": "s3cret"}, b"{}") is True
    assert tracker.verify_webhook(_basic("s3cret"), b"{}") is True
    assert tracker.verify_webhook(_basic("s3cret", user=""), b"{}") is True
    assert tracker.verify_webhook(_basic("other"), b"{}") is False
    assert tracker.verify_webhook({"Authorization": "Basic !!!not-base64"}, b"{}") is False
    assert tracker.verify_webhook({"Authorization": "Bearer s3cret"}, b"{}") is False
    assert tracker.verify_webhook({}, b"{}") is False
    assert shared_secret_of({}) == ""


def test_a_tracker_without_a_secret_refuses_every_delivery() -> None:
    client = AzureDevOpsClient(BASE, pat="x")
    assert AzureBoardsTracker(client, "Billing").verify_webhook(_basic(""), b"{}") is False


FIELDS = {
    "System.TeamProject": "Billing",
    "System.Title": "Credit notes",
    "System.State": "Active",
    "System.WorkItemType": "Bug",
    "System.Tags": "agent-ready; billing",
    "System.AreaPath": "Billing\\Team A",
    "System.Description": "<div>Total <b>ignores</b> it</div>",
    "System.CreatedBy": {"displayName": "Augustin", "uniqueName": "augustin@northwind.dev"},
}
CONTAINERS = {"collection": {"baseUrl": "https://dev.azure.com/northwind/"}}


def test_a_created_work_item_becomes_a_created_event() -> None:
    events = parse_azure_boards_event(
        "d-0",
        {
            "eventType": "workitem.created",
            "resource": {"id": 42, "fields": FIELDS},
            "resourceContainers": CONTAINERS,
        },
    )
    assert [e.type for e in events] == [InboundEventType.ITEM_CREATED]
    event = events[0]
    assert event.work_item_key == "42" and event.project_slug == "billing" and event.source == "azure-boards"
    assert event.payload["labels"] == ["agent-ready", "billing"]
    assert event.payload["body"] == "Total ignores it" and event.payload["item_type"] == "Bug"
    assert event.payload["url"] == "https://dev.azure.com/northwind/Billing/_workitems/edit/42"
    assert event.payload["reporter"] == "Augustin <augustin@northwind.dev>"
    assert event.payload["area_path"] == "Billing\\Team A"


def _updated(changes: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventType": "workitem.updated",
        "resource": {
            "id": 7,  # the update's own id: the work item is `workItemId`
            "workItemId": 42,
            "revisedBy": {"displayName": "Marie", "uniqueName": "marie@northwind.dev"},
            "fields": changes,
            "revision": {"fields": {**FIELDS, "System.State": "Closed"}},
        },
        "resourceContainers": CONTAINERS,
    }


def test_a_state_change_becomes_a_move() -> None:
    events = parse_azure_boards_event(
        "d-1", _updated({"System.State": {"oldValue": "Active", "newValue": "Closed"}})
    )
    assert [e.type for e in events] == [InboundEventType.ITEM_MOVED]
    assert events[0].work_item_key == "42", "the work item, not the update"
    assert (events[0].payload["from_status"], events[0].payload["to_status"]) == ("Active", "Closed")
    assert events[0].payload["status_category"] == "done"
    assert events[0].actor == "Marie <marie@northwind.dev>"


def test_an_added_tag_is_recognised_and_a_removed_one_too() -> None:
    added = parse_azure_boards_event(
        "d-2", _updated({"System.Tags": {"oldValue": "billing", "newValue": "billing; agent-ready"}})
    )
    assert added[0].type is InboundEventType.ITEM_LABELED
    assert added[0].payload["label"] == "agent-ready" and added[0].payload["removed"] is False
    removed = parse_azure_boards_event(
        "d-3", _updated({"System.Tags": {"oldValue": "billing; agent-ready", "newValue": "billing"}})
    )
    assert removed[0].payload["label"] is None and removed[0].payload["removed"] is True


def test_another_change_is_a_plain_update() -> None:
    events = parse_azure_boards_event("d-4", _updated({"System.Title": {"oldValue": "a", "newValue": "b"}}))
    assert [e.type for e in events] == [InboundEventType.ITEM_UPDATED]


def test_a_command_in_a_comment_becomes_a_decision() -> None:
    events = parse_azure_boards_event(
        "d-5",
        {
            "eventType": "workitem.commented",
            "resource": {
                "id": 9,
                "workItemId": 42,
                "revisedBy": {"displayName": "Augustin"},
                "revision": {"fields": {**FIELDS, "System.History": "<div>/choregos approve</div>"}},
            },
        },
    )
    assert [e.type for e in events] == [InboundEventType.ITEM_COMMENTED, InboundEventType.HUMAN_DECISION]
    assert events[0].payload["body"] == "/choregos approve"
    assert events[1].payload == {"verb": "approve", "argument": "", "channel": "tracker", "by": "Augustin"}


def test_a_deleted_work_item_is_a_closed_event_and_an_unknown_event_nothing() -> None:
    deleted = parse_azure_boards_event(
        "d-6", {"eventType": "workitem.deleted", "resource": {"id": 42, "fields": FIELDS}}
    )
    assert [e.type for e in deleted] == [InboundEventType.ITEM_CLOSED]
    assert parse_azure_boards_event("d-7", {"eventType": "git.push"}) == []
    assert parse_azure_boards_event("d-8", {}) == []


def test_parse_webhook_reads_the_body() -> None:
    tracker = build(FakeAzureBoardsServer())
    body = json.dumps({"eventType": "workitem.created", "id": "n-1", "resource": {"id": 1, "fields": FIELDS}})
    assert tracker.parse_webhook({}, body.encode())[0].delivery_id == "n-1"


# ───────────────────────────── html ─────────────────────────────


def test_markdown_is_escaped_before_it_becomes_html() -> None:
    html = markdown_to_html(
        "**<script>alert(1)</script>** [x](javascript:alert(1)) [ok](https://a.b/c?d=1&e=2)"
    )
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript' not in html and 'href="https://a.b/c?d=1&amp;e=2"' in html


def test_html_comes_back_as_text() -> None:
    assert html_to_text("<div>a &amp; b<br>c</div><p>d</p>") == "a & b\nc\nd"
    assert html_to_text(None) == ""
