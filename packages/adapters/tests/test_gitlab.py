"""The GitLab tracker: scoped labels, notes, metadata in the description, webhooks.

As for Jira, the tests speak the documented protocol through a simulated transport.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.gitlab import GitLabTracker
from choregos_adapters.tracker.gitlab_events import parse_gitlab_event
from choregos_contracts import InboundEventType, ProjectConfig, RepoConfig, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

PROJECT = "varga/billing-api"
ENCODED = "varga%2Fbilling-api"


def tracker(handler: Any) -> GitLabTracker:
    client = RestClient(
        "https://gitlab.com",
        headers={"PRIVATE-TOKEN": "token"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        service="gitlab",
    )
    return GitLabTracker(client, PROJECT, webhook_secret="s3cret")


def test_an_empty_project_is_refused_at_construction() -> None:
    with pytest.raises(ConfigurationError):
        GitLabTracker(RestClient("https://gitlab.com"), "")


@pytest.mark.parametrize(
    "block",
    ['{"Risk": "low", "Size": "M"}', '{"Risque": "low", "Taille": "M"}'],
    ids=["english-names", "block-from-before-S22-21"],
)
async def test_reading_a_ticket_with_metadata(block: str) -> None:
    description = f"Credit notes are not deducted.\n\n<!-- choregos:fields {block} -->\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/notes"):
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "author": {"username": "marie"}, "body": "an opinion", "system": False},
                    {"id": 2, "author": {"username": "gitlab"}, "body": "closed it", "system": True},
                ],
            )
        # GitLab requires the encoded project path: it is `raw_path` that goes out on the wire.
        assert request.url.raw_path.decode().startswith(f"/api/v4/projects/{ENCODED}/issues/12")
        assert request.headers["private-token"] == "token"
        return httpx.Response(
            200,
            json={
                "iid": 12,
                "title": "Avoirs",
                "description": description,
                "state": "opened",
                "labels": ["agent-ready", "choregos::implement"],
                "web_url": "https://gitlab.com/varga/billing-api/-/issues/12",
                "author": {"username": "augustin"},
                "assignees": [{"username": "marie"}],
            },
        )

    item = await tracker(handler).fetch_item(f"{PROJECT}#12")

    assert item.title == "Avoirs"
    assert item.size is Size.M and item.risk is Risk.LOW
    assert "choregos:fields" not in item.body, "the metadata does not pollute the body"
    assert [c.author for c in item.comments] == ["marie"], "system notes are dropped"


async def test_les_candidats_sont_les_issues_agent_ready() -> None:
    vu: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu.update(dict(request.url.params))
        return httpx.Response(200, json=[{"iid": 7}, {"iid": 9}])

    config = ProjectConfig(slug="billing-api", org="varga", repo=RepoConfig(url="https://x/y.git"))
    keys = await tracker(handler).list_candidates(config)

    assert keys == [f"{PROJECT}#7", f"{PROJECT}#9"]
    assert vu["labels"] == "agent-ready" and vu["state"] == "opened"


async def test_the_state_goes_through_a_scoped_label() -> None:
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_body.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await tracker(handler).set_state(
        f"{PROJECT}#12",
        TrackerStateMapping(label="choregos::verifying", remove_labels=["choregos::implement"]),
    )

    assert sent_body == {
        "add_labels": "choregos::verifying",
        "remove_labels": "choregos::implement",
    }


async def test_the_status_note_is_rewritten() -> None:
    from choregos_adapters.tracker.gitlab import STATUS_COMMENT_MARKER

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.raw_path.decode()))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "body": "an opinion"},
                    {"id": 5, "body": f"{STATUS_COMMENT_MARKER}\nancien"},
                ],
            )
        return httpx.Response(200, json={"id": 5})

    await tracker(handler).upsert_status_comment(f"{PROJECT}#12", "### Suivi\n\nnouveau")

    assert ("PUT", f"/api/v4/projects/{ENCODED}/issues/12/notes/5") in calls
    assert not any(method == "POST" for method, _ in calls)


async def test_the_fields_live_in_the_description() -> None:
    sent_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "iid": 12,
                    "description": 'Ticket body.\n\n<!-- choregos:fields {"Taille": "M"} -->',
                },
            )
        sent_body.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={})

    await tracker(handler).set_fields(f"{PROJECT}#12", {"Cost (€)": "1,25", "Run": "https://x/1"})

    description = sent_body["description"]
    assert description.startswith("Ticket body.")
    assert description.count("choregos:fields") == 1, "a single metadata block"
    payload = jsonlib.loads(description.split("choregos:fields", 1)[1].rsplit("-->", 1)[0].strip())
    # The block from before S22-21 (former French `Size`) is rewritten under the English name, no duplicate.
    assert payload == {"Cost (€)": "1,25", "Run": "https://x/1", "Size": "M"}


async def test_ticket_creation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = jsonlib.loads(request.content)
        assert body["labels"] == "finding,agent-ready"
        return httpx.Response(201, json={"iid": 31})

    key = await tracker(handler).create_item(
        NewItem(title="N+1 query", body="seen in review", labels=["finding", "agent-ready"])
    )
    assert key == f"{PROJECT}#31"


def test_the_token_protects_the_webhook() -> None:
    gitlab = tracker(lambda request: httpx.Response(200, json={}))
    assert gitlab.verify_webhook({"X-Gitlab-Token": "s3cret"}, b"{}") is True
    assert gitlab.verify_webhook({"X-Gitlab-Token": "autre"}, b"{}") is False


# ───────────────────────────── webhooks ─────────────────────────────


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "object_kind": "issue",
        "project": {"path_with_namespace": PROJECT, "web_url": f"https://gitlab.com/{PROJECT}"},
        "user": {"username": "marie"},
        "object_attributes": {"iid": 12, "title": "Avoirs", "action": "open", "state": "opened"},
        "labels": [{"title": "agent-ready"}],
    }
    payload.update(overrides)
    return payload


def test_an_opened_issue_becomes_an_event() -> None:
    events = parse_gitlab_event("Issue Hook", "u-1", _payload())
    assert [e.type for e in events] == [InboundEventType.ITEM_CREATED]
    assert events[0].work_item_key == f"{PROJECT}#12"
    assert events[0].project_slug == "billing-api"
    assert events[0].actor == "marie"
    # The user who opens an issue is its author (#1179).
    assert events[0].payload["reporter"] == "marie"


def test_an_added_label_is_recognised() -> None:
    events = parse_gitlab_event(
        "Issue Hook",
        "u-2",
        _payload(
            object_attributes={"iid": 12, "title": "Avoirs", "action": "update"},
            changes={"labels": {"previous": [], "current": [{"title": "agent-ready"}]}},
        ),
    )
    assert events[0].type is InboundEventType.ITEM_LABELED
    assert events[0].payload["label"] == "agent-ready"


def test_a_merged_merge_request_comes_up() -> None:
    events = parse_gitlab_event(
        "Merge Request Hook",
        "u-3",
        _payload(
            object_kind="merge_request",
            object_attributes={
                "iid": 4,
                "action": "merge",
                "state": "merged",
                "source_branch": "choregos/12-avoirs",
                "target_branch": "main",
                "last_commit": {"id": "abc123"},
                "url": "https://gitlab.com/x/-/merge_requests/4",
            },
        ),
    )
    assert events[0].type is InboundEventType.PR_MERGED
    assert events[0].work_item_key == f"{PROJECT}#12", "the branch designates the ticket"
    assert events[0].payload["sha"] == "abc123"


def test_a_failed_pipeline_comes_up_as_ci() -> None:
    events = parse_gitlab_event(
        "Pipeline Hook",
        "u-4",
        _payload(
            object_kind="pipeline",
            object_attributes={
                "id": 77,
                "status": "failed",
                "ref": "choregos/12-avoirs",
                "sha": "abc123",
            },
        ),
    )
    assert events[0].type is InboundEventType.CI_FAILED
    assert events[0].payload["pipeline_id"] == 77


def test_a_command_in_a_note_becomes_a_decision() -> None:
    events = parse_gitlab_event(
        "Note Hook",
        "u-5",
        _payload(
            object_kind="note",
            issue={"iid": 12},
            object_attributes={"note": "/choregos reject too risky", "url": "https://x#note_1"},
        ),
    )
    assert [e.type for e in events] == [
        InboundEventType.ITEM_COMMENTED,
        InboundEventType.HUMAN_DECISION,
    ]
    assert events[1].payload["verb"] == "reject"
    assert events[1].payload["argument"] == "too risky"


def test_a_note_outside_a_ticket_is_ignored() -> None:
    """A note on an MR or a commit is not a ticket decision."""
    events = parse_gitlab_event(
        "Note Hook",
        "u-6",
        _payload(object_kind="note", object_attributes={"note": "/choregos approve"}),
    )
    assert events == []


def test_an_unknown_event_produces_nothing() -> None:
    assert parse_gitlab_event("Wiki Page Hook", "u-7", {"object_kind": "wiki_page"}) == []


async def test_the_poll_also_searches_the_labels_of_the_start_rules() -> None:
    """S56-03: `labels_any` becomes one search per label, each with `labels_all`."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["labels"])
        return httpx.Response(200, json=[{"iid": len(seen)}])

    config = ProjectConfig.model_validate(
        {
            "slug": "billing-api",
            "org": "varga",
            "start_rules": [{"labels_all": ["ops"], "labels_any": ["p1", "p2"]}],
        }
    )
    keys = await tracker(handler).list_candidates(config)

    assert seen == ["agent-ready", "ops,p1", "ops,p2"]
    assert keys == [f"{PROJECT}#1", f"{PROJECT}#2", f"{PROJECT}#3"]
