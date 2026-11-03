"""The Jira tracker: what Choregos sends on the wire, and what it understands in return.

The tests speak the HTTP protocol documented by Atlassian through a simulated transport:
they prove the shape of the requests, not that a real Jira instance accepts them.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.adf import markdown_to_adf, text_of_adf
from choregos_adapters.tracker.jira import JiraTracker
from choregos_adapters.tracker.jira_events import parse_jira_event
from choregos_contracts import InboundEventType, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

BASE = "https://varga.atlassian.net"


def tracker(handler: Any) -> JiraTracker:
    client = RestClient(
        BASE,
        auth=("bot@varga.dev", "token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="jira",
    )
    return JiraTracker(client, "BILL", webhook_secret="s3cret")


def issue_payload(**overrides: Any) -> dict[str, Any]:
    fields = {
        "summary": "Credit notes are not deducted",
        "description": markdown_to_adf("When an order has a credit note, the total ignores it."),
        "status": {"name": "In Progress"},
        "labels": ["agent-ready"],
        "assignee": {"displayName": "Marie"},
        "reporter": {"displayName": "Augustin"},
        "Taille": {"value": "M"},
        "Risque": {"value": "low"},
        "comment": {"comments": []},
    }
    fields.update(overrides)
    return {"key": "BILL-42", "fields": fields}


async def test_reading_a_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/BILL-42"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json=issue_payload())

    item = await tracker(handler).fetch_item("BILL-42")

    assert item.title == "Credit notes are not deducted"
    assert "credit note" in item.body
    assert item.state == "In Progress"
    assert item.size is Size.M and item.risk is Risk.LOW
    assert item.url == f"{BASE}/browse/BILL-42"


async def test_the_candidates_go_through_a_jql_query() -> None:
    vu: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["jql"] = request.url.params.get("jql")
        return httpx.Response(200, json={"issues": [{"key": "BILL-7"}, {"key": "BILL-9"}]})

    from choregos_contracts import ProjectConfig, RepoConfig

    config = ProjectConfig(slug="bill", org="varga", repo=RepoConfig(url="https://x/y.git"))
    assert await tracker(handler).list_candidates(config) == ["BILL-7", "BILL-9"]
    assert 'project = "BILL"' in vu["jql"]
    assert 'labels = "agent-ready"' in vu["jql"]
    assert "statusCategory != Done" in vu["jql"]


async def test_a_dsl_state_becomes_a_transition() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "name": "Start", "to": {"name": "In Progress"}},
                        {"id": "21", "name": "Finish", "to": {"name": "Done"}},
                    ]
                },
            )
        return httpx.Response(204)

    await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="Done"))

    import json as jsonlib

    transition = next(c for c in calls if c[0] == "POST")
    sent_body = jsonlib.loads(transition[2])
    assert sent_body == {"transition": {"id": "21"}}, "the chosen transition is the one that leads to Done"


async def test_an_unreachable_status_is_a_readable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"transitions": [{"id": "11", "name": "Start", "to": {"name": "In Progress"}}]}
        )

    with pytest.raises(ConfigurationError) as error:
        await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="Deployed"))
    assert "Deployed" in str(error.value)
    assert "In Progress" in str(error.value), "the error says what was possible"


def _french_jira(calls: list[tuple[str, str, Any]]) -> Any:
    """The transitions as a French-language Jira site returns them (taken from a real site)."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {
                            "id": "11",
                            "name": "Backlog",
                            "to": {"name": "Backlog", "statusCategory": {"key": "new"}},
                        },
                        {
                            "id": "21",
                            "name": "En cours",  # lang: fr-ok (a French-speaking Jira site)
                            "to": {
                                "name": "En cours",  # lang: fr-ok (a French-speaking Jira site)
                                "statusCategory": {"key": "indeterminate"},
                            },
                        },
                        {
                            "id": "31",
                            "name": "Terminé(e)",
                            "to": {"name": "Terminé(e)", "statusCategory": {"key": "done"}},
                        },
                    ]
                },
            )
        return httpx.Response(204)

    return handler


async def test_a_french_jira_understands_the_templates_english_names() -> None:
    """`In Progress` must find its French-site counterpart: Jira translates the status names.

    The live test against a real French-speaking site failed exactly there, with the
    available statuses (Backlog, Selected for Development, and the translated ones).
    """
    import json as jsonlib

    calls: list[tuple[str, str, Any]] = []
    await tracker(_french_jira(calls)).set_state("BILL-42", TrackerStateMapping(status="In Progress"))
    sent_body = jsonlib.loads(next(c for c in calls if c[0] == "POST")[2])
    assert sent_body == {"transition": {"id": "21"}}, (
        "`In Progress` leads to the in-progress status by category"
    )

    calls.clear()
    await tracker(_french_jira(calls)).set_state("BILL-42", TrackerStateMapping(status="Done"))
    sent_body = jsonlib.loads(next(c for c in calls if c[0] == "POST")[2])
    assert sent_body == {"transition": {"id": "31"}}


async def test_the_exact_name_wins_over_the_category() -> None:
    import json as jsonlib

    calls: list[tuple[str, str, Any]] = []
    await tracker(_french_jira(calls)).set_state(
        "BILL-42",
        TrackerStateMapping(status="En cours"),  # lang: fr-ok (a French-speaking Jira site)
    )
    sent_body = jsonlib.loads(next(c for c in calls if c[0] == "POST")[2])
    assert sent_body == {"transition": {"id": "21"}}


async def test_an_explicit_category_is_understood() -> None:
    import json as jsonlib

    calls: list[tuple[str, str, Any]] = []
    await tracker(_french_jira(calls)).set_state("BILL-42", TrackerStateMapping(status="category:done"))
    sent_body = jsonlib.loads(next(c for c in calls if c[0] == "POST")[2])
    assert sent_body == {"transition": {"id": "31"}}


async def test_two_statuses_of_the_same_category_are_not_told_apart_by_chance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transitions": [
                    {
                        "id": "21",
                        "to": {"name": "En cours", "statusCategory": {"key": "indeterminate"}},  # lang: fr-ok
                    },
                    {"id": "22", "to": {"name": "En revue", "statusCategory": {"key": "indeterminate"}}},
                ]
            },
        )

    with pytest.raises(ConfigurationError) as error:
        await tracker(handler).set_state("BILL-42", TrackerStateMapping(status="In Progress"))
    message = str(error.value)
    assert "En cours" in message and "En revue" in message, (  # lang: fr-ok (a French-speaking Jira site)
        "the error names both candidates"
    )


async def test_the_status_comment_is_rewritten_not_duplicated() -> None:
    from choregos_adapters.tracker.jira import STATUS_COMMENT_MARKER

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {"id": "1", "body": markdown_to_adf("hello")},
                        {"id": "2", "body": markdown_to_adf(f"{STATUS_COMMENT_MARKER}\nold")},
                    ]
                },
            )
        return httpx.Response(200, json={"id": "2"})

    await tracker(handler).upsert_status_comment("BILL-42", "### Status\n\nnew")

    assert ("PUT", "/rest/api/3/issue/BILL-42/comment/2") in calls
    assert not any(method == "POST" for method, _ in calls), "no second comment"


async def test_the_status_table_goes_out_as_adf() -> None:
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"comments": []})
        import json as jsonlib

        sent_body.update(jsonlib.loads(request.content))
        return httpx.Response(201, json={"id": "9"})

    markdown = "### Choregos\n\n| Step | Cost |\n| --- | --- |\n| implement | 0.42 EUR |"
    await tracker(handler).upsert_status_comment("BILL-42", markdown)

    document = sent_body["body"]
    assert document["type"] == "doc"
    kinds = [node["type"] for node in document["content"]]
    assert "table" in kinds, "the table is not sent as raw markdown"
    assert "implement" in text_of_adf(document)


async def test_fields_absent_from_the_project_are_ignored() -> None:
    sent_body: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        if request.url.path == "/rest/api/3/field":
            return httpx.Response(
                200,
                json=[{"id": "customfield_1", "name": "Coût (€)"}],  # lang: fr-ok (former name)
            )
        sent_body.append(jsonlib.loads(request.content))
        return httpx.Response(204)

    await tracker(handler).set_fields("BILL-42", {"Coût (€)": 1.25, "Run": "https://x/run/1"})  # lang: fr-ok

    assert sent_body == [{"fields": {"customfield_1": 1.25}}], "only the existing field is written"


async def test_english_fields_go_into_the_french_fields_of_an_older_project() -> None:
    """S22-21: the orchestrator writes `Cost (€)`, `Size`, `Risk`; a Jira project set up before has only
    the former French names: the value goes there; if it has both, the English name wins."""
    sent_body: list[dict[str, Any]] = []
    board_fields = [
        {"id": "customfield_1", "name": "Coût (€)"},  # lang: fr-ok (former name)
        {"id": "customfield_2", "name": "Taille"},
        {"id": "customfield_3", "name": "Risque"},
        {"id": "customfield_4", "name": "Risk"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        if request.url.path == "/rest/api/3/field":
            return httpx.Response(200, json=board_fields)
        sent_body.append(jsonlib.loads(request.content))
        return httpx.Response(204)

    await tracker(handler).set_fields("BILL-42", {"Cost (€)": 1.25, "Size": "M", "Risk": "low"})

    assert sent_body == [{"fields": {"customfield_1": 1.25, "customfield_2": "M", "customfield_4": "low"}}]


def test_an_older_configuration_naming_the_former_size_stands_for_size() -> None:
    client = RestClient(BASE, auth=("bot@varga.dev", "token"), service="jira")
    jira = JiraTracker(client, "BILL", field_names={"Taille": "T-shirt"})
    assert jira.field_names["Size"] == "T-shirt" and "Taille" not in jira.field_names


async def test_ticket_creation_and_link() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/rest/api/3/issue":
            return httpx.Response(201, json={"key": "BILL-77"})
        return httpx.Response(201, json={})

    jira = tracker(handler)
    key = await jira.create_item(NewItem(title="Connection leak", body="Found in review"))
    assert key == "BILL-77"

    await jira.link("BILL-77", "BILL-42", "origin")
    assert ("POST", "/rest/api/3/issueLink") in calls


async def test_a_jira_failure_is_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="token revoked")

    with pytest.raises(UpstreamError) as error:
        await tracker(handler).fetch_item("BILL-42")
    assert "jira" in str(error.value)
    assert error.value.status_code == 403


def test_the_shared_secret_protects_the_webhook() -> None:
    jira = tracker(lambda request: httpx.Response(200, json={}))
    assert jira.verify_webhook({"X-Choregos-Secret": "s3cret"}, b"{}") is True
    assert jira.verify_webhook({"X-Choregos-Secret": "other"}, b"{}") is False
    assert jira.verify_webhook({}, b"{}") is False


# ───────────────────────────── webhooks ─────────────────────────────


def test_a_status_change_becomes_a_move() -> None:
    events = parse_jira_event(
        "d-1",
        {
            "webhookEvent": "jira:issue_updated",
            "user": {"displayName": "Marie"},
            "issue": {"key": "BILL-42", "fields": {"summary": "titre", "status": {"name": "Done"}}},
            "changelog": {"items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
        },
    )

    assert [e.type for e in events] == [InboundEventType.ITEM_MOVED]
    assert events[0].work_item_key == "BILL-42"
    assert events[0].project_slug == "bill"
    assert events[0].payload["to_status"] == "Done"
    assert events[0].actor == "Marie"


def test_an_added_label_is_recognised() -> None:
    events = parse_jira_event(
        "d-2",
        {
            "webhookEvent": "jira:issue_updated",
            "issue": {"key": "BILL-42", "fields": {"summary": "titre"}},
            "changelog": {
                "items": [{"field": "labels", "fromString": "urgent", "toString": "urgent agent-ready"}]
            },
        },
    )

    assert events[0].type is InboundEventType.ITEM_LABELED
    assert events[0].payload["label"] == "agent-ready"
    assert events[0].payload["removed"] is False


def test_a_command_in_a_comment_becomes_a_decision() -> None:
    events = parse_jira_event(
        "d-3",
        {
            "webhookEvent": "comment_created",
            "issue": {"key": "BILL-42", "fields": {"summary": "titre"}},
            "comment": {
                "author": {"displayName": "Augustin"},
                "body": markdown_to_adf("/choregos approve"),
            },
        },
    )

    assert [e.type for e in events] == [
        InboundEventType.ITEM_COMMENTED,
        InboundEventType.HUMAN_DECISION,
    ]
    assert events[1].payload == {
        "verb": "approve",
        "argument": "",
        "channel": "tracker",
        "by": "Augustin",
    }


def test_an_unknown_event_produces_nothing() -> None:
    assert parse_jira_event("d-4", {"webhookEvent": "sprint_started"}) == []
    assert parse_jira_event("d-5", {}) == []


async def test_the_poll_also_asks_for_the_project_s_start_rules() -> None:
    """S56-03: each start rule is a JQL clause ORed with `agent-ready`; a quote cannot end it."""
    vu: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["jql"] = request.url.params.get("jql")
        return httpx.Response(200, json={"issues": []})

    from choregos_contracts import ProjectConfig

    config = ProjectConfig.model_validate(
        {
            "slug": "bill",
            "org": "varga",
            "start_rules": [
                {"item_type": "Incident", "states": ["New", "1"]},
                {"labels_any": ["payroll", 'x" OR project = "OTHER']},
            ],
        }
    )
    await tracker(handler).list_candidates(config)

    assert vu["jql"] == (
        'project = "BILL" AND (labels = "agent-ready" OR (issuetype = "Incident" AND status in ("New", "1")) '
        'OR (labels in ("payroll", "x\\" OR project = \\"OTHER"))) '
        "AND statusCategory != Done ORDER BY updated DESC"
    )


# ───────── a scoped token, and the connector's Test (service accounts) ─────────


def _scoped(handler: Any, cloud_id: str | None = None) -> JiraTracker:
    from choregos_adapters.tracker.jira import JiraGatewayClient

    client = JiraGatewayClient(
        BASE,
        cloud_id=cloud_id,
        auth=("svc@serviceaccount.atlassian.com", "jeton"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="jira",
    )
    return JiraTracker(client, "BILL")


async def test_a_scoped_token_goes_through_the_gateway_and_finds_the_cloud_id() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/_edge/tenant_info":
            assert "authorization" not in request.headers  # a public endpoint: no token sent
            return httpx.Response(200, json={"cloudId": "c-123"})
        return httpx.Response(200, json=issue_payload())

    jira = _scoped(handler)
    item = await jira.fetch_item("BILL-42")
    await jira.fetch_item("BILL-42")
    assert calls[0] == f"{BASE}/_edge/tenant_info"
    assert calls[1].startswith("https://api.atlassian.com/ex/jira/c-123/rest/api/3/issue/BILL-42")
    assert len(calls) == 3  # the cloud id is read once
    assert item.url == f"{BASE}/browse/BILL-42"  # a person opens the site, not the gateway


async def test_a_given_cloud_id_is_not_looked_up() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"name": "Billing"})

    result = await _scoped(handler, cloud_id="c-9").test()
    assert result == {"ok": True, "project": "Billing", "cloud_id": "c-9"}
    assert calls == ["https://api.atlassian.com/ex/jira/c-9/rest/api/3/project/BILL"]


async def test_an_unreadable_site_names_the_cloud_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(ConfigurationError, match="cloud_id"):
        await _scoped(handler).fetch_item("BILL-42")


async def test_the_connector_test_reads_the_project() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/project/BILL"
        return httpx.Response(200, json={"name": "Billing"})

    assert await tracker(handler).test() == {"ok": True, "project": "Billing"}


@pytest.mark.parametrize(
    ("status", "says"),
    [(401, "auth: scoped"), (403, "read:jira-work"), (404, "add it to the project"), (400, "400")],
)
async def test_a_refused_test_says_what_to_fix(status: int, says: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    result = await tracker(handler).test()
    assert result["ok"] is False
    assert says in result["reason"]


def test_the_registry_builds_a_scoped_jira_on_the_gateway() -> None:
    from choregos_adapters import _jira_tracker  # the registered builder: the suite runs on fakes
    from choregos_adapters.tracker.jira import JiraGatewayClient

    config = {"base_url": BASE, "email": "svc@x", "api_token": "t", "project_key": "BILL"}
    classic = _jira_tracker(config)
    scoped = _jira_tracker({**config, "auth": "scoped", "cloud_id": "c-1"})
    assert not isinstance(classic.client, JiraGatewayClient)
    assert scoped.client.base_url == "https://api.atlassian.com/ex/jira/c-1"
    assert scoped.site_url == BASE


def test_a_scoped_jira_opens_the_gateway_in_its_egress() -> None:
    from choregos_adapters.registry import egress_hosts

    hosts = egress_hosts("tracker", "jira", {"base_url": BASE, "auth": "scoped"})
    assert ("api.atlassian.com", 443) in hosts and ("varga.atlassian.net", 443) in hosts


async def test_a_connector_jql_selects_the_candidates_without_the_agent_ready_label() -> None:
    # The factory itself, not `build`: under CHOREGOS_FAKES=1 (the CI) `build` hands out the fake.
    from choregos_adapters import _jira_tracker
    from choregos_contracts import ProjectConfig

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.params.get("jql")))
        return httpx.Response(200, json={"issues": [{"key": "BILL-12"}]})

    adapter = _jira_tracker(
        {
            "base_url": BASE,
            "email": "bot@varga.dev",
            "api_token": "token",
            "project_key": "BILL",
            "candidates_jql": " parent = BILL-1 AND labels in (form-a, form-b) ",
        }
    )
    adapter.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    keys = await adapter.list_candidates(ProjectConfig(slug="bill", org="varga"))

    assert keys == ["BILL-12"]
    assert seen == [
        'project = "BILL" AND (parent = BILL-1 AND labels in (form-a, form-b)) '
        "AND statusCategory != Done ORDER BY updated DESC"
    ]


async def test_without_a_connector_jql_the_candidates_are_the_agent_ready_issues() -> None:
    from choregos_contracts import ProjectConfig

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.params.get("jql")))
        return httpx.Response(200, json={"issues": []})

    jira = tracker(handler)
    jira.candidates_jql = None
    await jira.list_candidates(ProjectConfig(slug="bill", org="varga"))

    assert seen == [
        'project = "BILL" AND labels = "agent-ready" AND statusCategory != Done ORDER BY updated DESC'
    ]


async def test_the_reporter_is_named_with_their_email_when_the_site_shows_it() -> None:
    """Who filed the issue reaches the work item (#1179): an access request is checked against it."""
    shown = {"displayName": "Jean Martin", "emailAddress": "jmartin@varga.dev", "accountId": "712020:abc"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=issue_payload(reporter=shown))

    item = await tracker(handler).fetch_item("BILL-42")
    assert item.author == "Jean Martin <jmartin@varga.dev>"


async def test_a_hidden_email_leaves_the_reporter_s_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=issue_payload())

    assert (await tracker(handler).fetch_item("BILL-42")).author == "Augustin"


def test_a_created_issue_carries_its_reporter() -> None:
    events = parse_jira_event(
        "d-9",
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "BILL-43",
                "fields": {
                    "summary": "titre",
                    "labels": ["agent-ready"],
                    "reporter": {"displayName": "Jean Martin", "emailAddress": "jmartin@varga.dev"},
                },
            },
        },
    )

    assert events[0].type is InboundEventType.ITEM_CREATED
    assert events[0].payload["reporter"] == "Jean Martin <jmartin@varga.dev>"
