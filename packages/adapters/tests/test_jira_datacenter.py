"""The Jira Data Center / Server flavour: REST v2, wiki markup, bearer token, users by name, webhooks.

Simulated transport only: the tests prove the shape of the requests the flavour sends and what it
understands in return, not that a real Data Center instance accepts them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tranibor_adapters.builtins import _jira_tracker
from tranibor_adapters.errors import ConfigurationError
from tranibor_adapters.http import RestClient
from tranibor_adapters.specs import _check_jira
from tranibor_adapters.tracker.jira import JiraTracker
from tranibor_adapters.tracker.wiki import markdown_to_wiki, text_of_wiki
from tranibor_contracts import InboundEventType
from tranibor_core.domain import NewItem, TrackerStateMapping

BASE = "https://jira.corp.example/jira"  # a context path, as Data Center is often served


def tracker(handler: Any, **kwargs: Any) -> JiraTracker:
    client = RestClient(
        BASE,
        headers={"Authorization": "Bearer pat-123"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="jira",
    )
    return JiraTracker(client, "BILL", webhook_secret="s3cret", flavor="datacenter", **kwargs)


def test_markdown_to_wiki_covers_the_status_comment_subset() -> None:
    wiki = markdown_to_wiki(
        "## Status\n\nDone **now** with `code` and [docs](https://x.y/z)\n\n- one\n- two\n\n"
        "| Stage | State |\n|---|---|\n| build | ok |\n"
    )
    assert "h2. Status" in wiki
    assert "*now*" in wiki and "{{code}}" in wiki and "[docs|https://x.y/z]" in wiki
    assert "* one\n* two" in wiki
    assert "||Stage||State||" in wiki and "|build|ok|" in wiki


def test_the_marker_survives_the_wiki_escaping() -> None:
    wiki = markdown_to_wiki("[choregos:status]\nbody")
    assert "\\[" in wiki  # escaped, so Jira does not render a link
    assert "[choregos:status]" in text_of_wiki(wiki)


async def test_reading_an_issue_uses_v2_and_a_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jira/rest/api/2/issue/BILL-42"
        assert request.headers["authorization"] == "Bearer pat-123"
        return httpx.Response(
            200,
            json={
                "key": "BILL-42",
                "names": {"customfield_10": "Taille"},
                "fields": {
                    "summary": "Credit notes",
                    "description": "h1. Why\nThe total ignores them.",
                    "status": {"name": "In Progress"},
                    "labels": [],
                    "reporter": {"name": "mdupont", "key": "JIRAUSER1", "displayName": "Marie D."},
                    "assignee": {"name": "mdupont", "displayName": "Marie D."},
                    "comment": {
                        "comments": [
                            {
                                "id": "7",
                                "author": {"name": "bot", "displayName": "Bot"},
                                "body": "\\[choregos:status\\]\nall green",
                            }
                        ]
                    },
                },
            },
        )

    item = await tracker(handler).fetch_item("BILL-42")
    assert item.body == "h1. Why\nThe total ignores them."
    assert item.url == f"{BASE}/browse/BILL-42"
    assert item.comments[0].marker == "[choregos:status]"
    # A user is its login or key: no accountId exists on Data Center.
    assert item.fields["tracker"]["reporter"]["account_id"] == "JIRAUSER1"


async def test_writes_send_wiki_and_name_the_assignee_by_login() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(201, json={"key": "BILL-1", "id": "9"})

    t = tracker(handler)
    key = await t.create_item(NewItem(title="T", body="## H\n- a", labels=["x"], assignees=["mdupont"]))
    await t.comment("BILL-1", "**hi**")
    assert key == "BILL-1"
    create = seen[0]
    assert create[1] == "/jira/rest/api/2/issue"
    assert create[2]["fields"]["description"] == "h2. H\n* a"
    assert create[2]["fields"]["assignee"] == {"name": "mdupont"}
    assert seen[1][1] == "/jira/rest/api/2/issue/BILL-1/comment" and seen[1][2] == {"body": "*hi*"}


async def test_the_status_comment_is_found_again_and_rewritten() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"comments": [{"id": "5", "body": "\\[choregos:status\\]\nold"}]})
        return httpx.Response(200, json={})

    await tracker(handler).upsert_status_comment("BILL-1", "new")
    assert calls[-1] == ("PUT", "/jira/rest/api/2/issue/BILL-1/comment/5")


async def test_candidates_use_the_classic_search_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"issues": [{"key": "BILL-3"}]})

    from tranibor_contracts import ProjectConfig

    project = ProjectConfig(slug="bill", org="northwind")
    assert await tracker(handler).list_candidates(project) == ["BILL-3"]
    assert paths == ["/jira/rest/api/2/search"]


async def test_transitions_and_custom_fields_discovered_by_name() -> None:
    puts: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200, json={"transitions": [{"id": "31", "name": "Go", "to": {"name": "Done"}}]}
            )
        if path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_10100", "name": "Size"}])
        puts.append((request.method, path, json.loads(request.content)))
        return httpx.Response(204)

    t = tracker(handler)
    await t.set_state("BILL-1", TrackerStateMapping(status="Done"))
    await t.set_fields("BILL-1", {"Size": "M", "Unknown": "x"})
    assert puts[0] == ("POST", "/jira/rest/api/2/issue/BILL-1/transitions", {"transition": {"id": "31"}})
    assert puts[1][2] == {"fields": {"customfield_10100": "M"}}


async def test_the_connector_test_checks_the_server_version() -> None:
    def make(version: dict[str, Any]) -> JiraTracker:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/serverInfo"):
                return httpx.Response(200, json=version)
            return httpx.Response(200, json={"name": "Billing"})

        return tracker(handler)

    ok = await make({"version": "9.12.4", "versionNumbers": [9, 12, 4]}).test()
    assert ok == {"ok": True, "project": "Billing", "version": "9.12.4"}
    assert (await make({"version": "10.3.0"}).test())["ok"] is True  # dotted string, no numbers
    old = await make({"version": "8.20.1", "versionNumbers": [8, 20, 1]}).test()
    assert old["ok"] is False and "9.0" in old["reason"]
    assert (await make({}).test())["ok"] is False


def _signed(body: bytes, secret: str = "s3cret") -> dict[str, str]:
    return {"X-Hub-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def test_webhook_signature_is_verified_and_cannot_be_downgraded() -> None:
    t = tracker(lambda r: httpx.Response(200))
    body = b'{"webhookEvent": "jira:issue_created"}'
    assert t.verify_webhook(_signed(body), body)
    assert not t.verify_webhook(_signed(body, "other"), body)
    assert not t.verify_webhook(_signed(b"{}"), body)
    # A bad signature is not rescued by a correct shared-secret header.
    assert not t.verify_webhook({**_signed(body, "other"), "X-Choregos-Secret": "s3cret"}, body)
    # A DC without signing still works through the shared-secret header, as on Cloud.
    assert t.verify_webhook({"x-choregos-secret": "s3cret"}, body)
    assert not t.verify_webhook({}, body)


def test_a_dc_webhook_is_normalised_with_a_stable_delivery_id() -> None:
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"name": "mdupont"},
        "issue": {"key": "BILL-5", "fields": {"summary": "S", "status": {"name": "Done"}}},
        "changelog": {"items": [{"field": "status", "fromString": "To Do", "toString": "Done"}]},
    }
    body = json.dumps(payload).encode()
    t = tracker(lambda r: httpx.Response(200))
    first = t.parse_webhook({}, body)
    again = t.parse_webhook({}, body)
    assert first[0].type is InboundEventType.ITEM_MOVED
    assert first[0].actor == "mdupont"
    assert first[0].delivery_id == again[0].delivery_id and first[0].delivery_id


def test_the_cloud_webhook_still_requires_its_header() -> None:
    cloud = JiraTracker(RestClient("https://x.atlassian.net"), "BILL", webhook_secret="s3cret")
    body = b"{}"
    assert not cloud.verify_webhook(_signed(body), body)  # a DC signature means nothing on Cloud
    assert cloud.verify_webhook({"X-Choregos-Secret": "s3cret"}, body)


def test_the_builtin_builds_a_bearer_client_without_an_email() -> None:
    built = _jira_tracker(
        {"flavor": "datacenter", "base_url": BASE, "project_key": "bill", "api_token": "pat-9"}
    )
    assert built.flavor == "datacenter"
    assert built.client._headers["Authorization"] == "Bearer pat-9"  # type: ignore[attr-defined]
    cloud = _jira_tracker(
        {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "t", "project_key": "B"}
    )
    assert cloud.flavor == "cloud"


def test_configuration_errors_are_loud(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="flavor"):
        _jira_tracker({"flavor": "server2", "base_url": BASE, "project_key": "B", "api_token": "t"})
    with pytest.raises(ConfigurationError, match="ca_bundle"):
        _jira_tracker(
            {
                "flavor": "datacenter",
                "base_url": BASE,
                "project_key": "B",
                "api_token": "t",
                "ca_bundle": "/nope.pem",
            }
        )
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate")
    with pytest.raises(ConfigurationError, match="no usable certificate"):
        _jira_tracker(
            {
                "flavor": "datacenter",
                "base_url": BASE,
                "project_key": "B",
                "api_token": "t",
                "ca_bundle": str(junk),
            }
        )
    with pytest.raises(ConfigurationError, match="email"):
        _check_jira({"base_url": BASE, "project_key": "B"})
    with pytest.raises(ConfigurationError, match="Cloud settings"):
        _check_jira({"flavor": "datacenter", "auth": "scoped"})
    _check_jira({"flavor": "datacenter", "base_url": BASE, "project_key": "B"})  # no email needed
