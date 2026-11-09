"""The GitHub tracker: issues through REST, a Projects v2 board through GraphQL, signed webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.github.client import GitHubClient
from choregos_adapters.tracker.github import STATUS_COMMENT_MARKER, GitHubTracker
from choregos_adapters.tracker.github_events import parse_command, parse_github_event
from choregos_contracts import InboundEventType, ProjectConfig, Risk, Size
from choregos_core.domain import NewItem, TrackerStateMapping

from ._transport import Wire

API = "https://api.github.test"
REPO = "varga/billing"


def tracker(wire: Wire, **kwargs: Any) -> GitHubTracker:
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=wire.client())
    return GitHubTracker(client, REPO, webhook_secret="s3cret", **kwargs)


def test_a_repository_without_an_owner_is_refused_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="owner/name"):
        GitHubTracker(GitHubClient(token="t"), "billing")


async def test_reading_a_ticket_translates_labels_size_and_risk() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12"): (
                200,
                {
                    "title": "Credit notes",
                    "body": None,
                    "html_url": "u",
                    "state": "open",
                    "labels": [{"name": "agent-ready"}, {"name": "size:L"}, {"name": "risk:HIGH"}],
                    "assignees": [{"login": "marie"}],
                    "user": {"login": "paul"},
                },
            ),
            ("GET", f"/repos/{REPO}/issues/12/comments"): (
                200,
                [{"id": 1, "user": {"login": "bot"}, "body": f"{STATUS_COMMENT_MARKER}\nstate"}],
            ),
        }
    )
    item = await tracker(wire).fetch_item(f"{REPO}#12")
    assert item.title == "Credit notes" and item.body == "" and item.author == "paul"
    assert item.size == Size.L and item.risk == Risk.HIGH
    assert item.comments[0].marker == STATUS_COMMENT_MARKER


async def test_the_candidates_are_the_agent_ready_issues_without_the_prs() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues"): (
                200,
                [{"number": 1}, {"number": 2, "pull_request": {"url": "x"}}, {"number": 3}],
            )
        }
    )
    keys = await tracker(wire).list_candidates(ProjectConfig(slug="billing", org="varga"))
    assert keys == [f"{REPO}#1", f"{REPO}#3"], "GitHub lists PRs among the issues: they are dropped"
    assert wire.requests[0].url.params["labels"] == "agent-ready"


async def test_a_state_replaces_the_scoped_label_and_keeps_the_others() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12"): (
                200,
                {"labels": [{"name": "choregos:inbox"}, {"name": "agent-ready"}, {"name": "bug"}]},
            ),
            ("PUT", f"/repos/{REPO}/issues/12/labels"): (200, {}),
        }
    )
    await tracker(wire).set_state(
        f"{REPO}#12", TrackerStateMapping(label="choregos:in_progress", remove_labels=["agent-ready"])
    )
    assert wire.body(-1) == {"labels": ["bug", "choregos:in_progress"]}


async def test_closing_an_open_issue_closes_it_as_completed() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12"): (200, {"state": "open"}),
            ("PATCH", f"/repos/{REPO}/issues/12"): (200, {}),
        }
    )
    await tracker(wire).close_item(f"{REPO}#12")
    assert wire.sent("PATCH") == [("PATCH", f"/repos/{REPO}/issues/12")]
    assert wire.body(-1) == {"state": "closed", "state_reason": "completed"}


async def test_closing_a_closed_issue_does_nothing() -> None:
    wire = Wire({("GET", f"/repos/{REPO}/issues/12"): (200, {"state": "closed"})})
    await tracker(wire).close_item(f"{REPO}#12")
    assert wire.sent("PATCH") == []


async def test_the_status_comment_is_rewritten_not_duplicated() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12/comments"): (
                200,
                [{"id": 5, "body": "bonjour"}, {"id": 6, "body": f"{STATUS_COMMENT_MARKER}\nancien"}],
            ),
            ("PATCH", f"/repos/{REPO}/issues/comments/6"): (200, {}),
        }
    )
    await tracker(wire).upsert_status_comment(f"{REPO}#12", "nouveau")
    assert wire.sent("PATCH") == [("PATCH", f"/repos/{REPO}/issues/comments/6")]
    assert wire.body(-1)["body"] == f"{STATUS_COMMENT_MARKER}\nnouveau"
    assert wire.sent("POST") == []


async def test_without_a_status_comment_one_is_created() -> None:
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12/comments"): (200, []),
            ("POST", f"/repos/{REPO}/issues/12/comments"): (201, {"id": 9}),
        }
    )
    await tracker(wire).upsert_status_comment(f"{REPO}#12", "premier")
    assert wire.body(-1)["body"].startswith(STATUS_COMMENT_MARKER)


async def test_creating_a_ticket_returns_a_readable_key_and_links_are_comments() -> None:
    wire = Wire(
        {
            ("POST", f"/repos/{REPO}/issues"): (201, {"number": 40}),
            ("POST", f"/repos/{REPO}/issues/40/comments"): (201, {"id": 1}),
        }
    )
    t = tracker(wire)
    key = await t.create_item(NewItem(title="Profiles base", body="…", labels=["finding"]))
    assert key == f"{REPO}#40"
    await t.link(key, f"{REPO}#12", "origin")
    assert "<!-- choregos:link:origin -->" in wire.body(-1)["body"] and "#12" in wire.body(-1)["body"]


def _project(options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "organization": {
            "projectV2": {
                "id": "PVT_1",
                "fields": {
                    "nodes": [
                        {"id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT", "options": options},
                        {
                            "id": "F_cost",
                            "name": "Coût (€)",  # lang: fr-ok (former name)
                            "dataType": "NUMBER",
                        },
                    ]
                },
            }
        }
    }


async def test_the_board_receives_the_column_and_creates_the_missing_option() -> None:
    """The DSL states drive the board: a missing column is created, not invented elsewhere."""
    graphql = [
        (200, {"data": _project([{"id": "O_inbox", "name": "Inbox"}])}),  # board_fields du board
        (
            200,
            {
                "data": {
                    "node": {
                        "items": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [{"id": "I_1", "content": {"number": 12}}],
                        }
                    }
                }
            },
        ),
        (
            200,
            {
                "data": {
                    "updateProjectV2Field": {
                        "projectV2Field": {
                            "options": [
                                {"id": "O_inbox", "name": "Inbox"},
                                {"id": "O_new", "name": "In progress"},
                            ]
                        }
                    }
                }
            },
        ),
        (200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "I_1"}}}}),
    ]
    graphql.insert(
        2, (200, {"data": {"node": {"id": "F_status", "options": [{"id": "O_inbox", "name": "Inbox"}]}}})
    )
    wire = Wire({("POST", "/graphql"): graphql})
    await tracker(wire, project_number=3).set_state(f"{REPO}#12", TrackerStateMapping(status="In progress"))
    request_bodies = [json.loads(r.content) for r in wire.requests]
    assert request_bodies[0]["variables"] == {"org": "varga", "number": 3}
    assert request_bodies[3]["variables"]["options"][-1]["name"] == "In progress", "the option is created"
    assert request_bodies[4]["variables"]["value"] == {"singleSelectOptionId": "O_new"}
    assert request_bodies[4]["variables"]["itemId"] == "I_1"


def _board_items() -> tuple[int, Any]:
    return (
        200,
        {
            "data": {
                "node": {
                    "items": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"id": "I_1", "content": {"number": 12}}],
                    }
                }
            }
        },
    )


async def test_creating_an_option_keeps_all_the_others_with_their_id() -> None:
    """#350: `singleSelectOptions` REPLACES the list, so the mutation carries every option."""
    existing = [
        {"id": "O_inbox", "name": "Inbox", "color": "BLUE", "description": "to triage"},
        {"id": "O_done", "name": "Done", "color": "GREEN", "description": ""},
    ]
    # The board cache is stale: *Done* was added since, the fresh read sees it.
    graphql = [
        (200, {"data": _project([existing[0]])}),
        _board_items(),
        (200, {"data": {"node": {"id": "F_status", "options": existing}}}),
        (
            200,
            {
                "data": {
                    "updateProjectV2Field": {
                        "projectV2Field": {"options": [*existing, {"id": "O_new", "name": "In progress"}]}
                    }
                }
            },
        ),
        (200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "I_1"}}}}),
    ]
    wire = Wire({("POST", "/graphql"): graphql})
    await tracker(wire, project_number=3).set_state(f"{REPO}#12", TrackerStateMapping(status="In progress"))
    mutation = next(
        json.loads(r.content)
        for r in wire.requests
        if "updateProjectV2Field(" in json.loads(r.content)["query"]
    )
    assert mutation["variables"]["options"] == [
        {"id": "O_inbox", "name": "Inbox", "color": "BLUE", "description": "to triage"},
        {"id": "O_done", "name": "Done", "color": "GREEN", "description": ""},
        {"name": "In progress", "color": "GRAY", "description": ""},
    ], "existing options are sent with their id, colour and description"
    assert wire.body(-1)["variables"]["value"] == {"singleSelectOptionId": "O_new"}


async def test_an_option_added_since_the_cache_is_not_created_again() -> None:
    """Idempotent: the option already exists when the field is read again, no field mutation."""
    graphql = [
        (200, {"data": _project([{"id": "O_inbox", "name": "Inbox"}])}),
        _board_items(),
        (
            200,
            {
                "data": {
                    "node": {
                        "id": "F_status",
                        "options": [
                            {"id": "O_inbox", "name": "Inbox"},
                            {"id": "O_run", "name": "In progress"},
                        ],
                    }
                }
            },
        ),
        (200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "I_1"}}}}),
    ]
    wire = Wire({("POST", "/graphql"): graphql})
    await tracker(wire, project_number=3).set_state(f"{REPO}#12", TrackerStateMapping(status="In progress"))
    queries = [json.loads(r.content)["query"] for r in wire.requests]
    assert not any("updateProjectV2Field(" in q for q in queries), "the option exists: nothing to create"
    assert wire.body(-1)["variables"]["value"] == {"singleSelectOptionId": "O_run"}


async def test_a_ticket_absent_from_the_board_is_added_and_a_number_goes_out_as_a_number() -> None:
    graphql = [
        (200, {"data": _project([])}),
        (200, {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}}),
        (200, {"data": {"addProjectV2ItemById": {"item": {"id": "I_new"}}}}),
        (200, {"data": {}}),
    ]
    wire = Wire(
        {("POST", "/graphql"): graphql, ("GET", f"/repos/{REPO}/issues/12"): (200, {"node_id": "ISSUE_12"})}
    )
    await tracker(wire, project_number=3).set_fields(
        f"{REPO}#12",
        {"Coût (€)": "1.5", "Unknown": "x"},  # lang: fr-ok
    )
    request_bodies = [json.loads(r.content) for r in wire.requests if r.url.path == "/graphql"]
    assert request_bodies[2]["variables"]["contentId"] == "ISSUE_12"
    assert request_bodies[3]["variables"]["value"] == {"number": 1.5}
    assert len(request_bodies) == 4, "the field `Unknown` does not exist on this board: nothing is invented"


def _board(board_fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"organization": {"projectV2": {"id": "PVT_1", "fields": {"nodes": board_fields}}}}}


#: A board provisioned before S22-21: its fields carry their former French names.
OLDER_BOARD = [
    {"id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT", "options": []},
    {"id": "F_cost", "name": "Coût (€)", "dataType": "NUMBER"},  # lang: fr-ok (former name)
    {"id": "F_size", "name": "Taille", "dataType": "SINGLE_SELECT", "options": [{"id": "O_m", "name": "M"}]},
    {"id": "F_risk", "name": "Risque", "dataType": "SINGLE_SELECT", "options": []},
]


async def test_provisioning_renames_the_french_fields_instead_of_doubling_them() -> None:
    """S22-21: the former French names become `Cost (€)`, `Size`, `Risk`, the same field,
    so the values already set on the cards stay; `Run`, absent, is created."""
    graphql: list[Any] = [(200, _board(OLDER_BOARD))] + [(200, {"data": {}})] * 4
    wire = Wire({("POST", "/graphql"): graphql})
    report = await tracker(wire, project_number=3).ensure_project_fields(
        ["Status", "Cost (€)", "Size", "Risk", "Run"]
    )
    mutations = [json.loads(r.content) for r in wire.requests][1:]
    renames = [m["variables"] for m in mutations if "createProjectV2Field" not in m["query"]]
    assert renames == [
        {"fieldId": "F_cost", "name": "Cost (€)"},
        {"fieldId": "F_size", "name": "Size"},
        {"fieldId": "F_risk", "name": "Risk"},
    ], "the former field is renamed, not doubled"
    creations = [m["variables"] for m in mutations if "createProjectV2Field" in m["query"]]
    assert creations == [{"projectId": "PVT_1", "name": "Run", "dataType": "TEXT"}]
    assert report == {
        "kept": ["Status"],
        "renamed": ["Coût (€) → Cost (€)", "Taille → Size", "Risque → Risk"],  # lang: fr-ok (former name)
        "created": ["Run"],
    }


async def test_an_older_template_naming_the_fields_in_french_gets_the_english_names() -> None:
    """Replayable: a board already in English does not move, even if the step still names the old `Size`."""
    board = [
        {"id": "F_status", "name": "Status", "dataType": "SINGLE_SELECT", "options": []},
        {"id": "F_size", "name": "Size", "dataType": "SINGLE_SELECT", "options": []},
    ]
    wire = Wire({("POST", "/graphql"): [(200, _board(board)), (200, {"data": {}})]})
    report = await tracker(wire, project_number=3).ensure_project_fields(
        ["Status", "Taille", "Risque"]
    )  # lang: fr-ok
    mutations = [json.loads(r.content) for r in wire.requests][1:]
    assert [m["variables"]["name"] for m in mutations] == ["Risk"]
    assert mutations[0]["variables"]["options"] == [
        {"name": n, "color": "GRAY", "description": ""} for n in ("low", "medium", "high", "critical")
    ]
    assert report == {"kept": ["Status", "Size"], "renamed": [], "created": ["Risk"]}


async def test_an_older_board_not_yet_renamed_still_receives_its_values() -> None:
    """For one release, `Cost (€)`/`Size` go into the former French fields if the board has only those."""
    graphql = [
        (200, _board(OLDER_BOARD)),
        (200, {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}}),
        (200, {"data": {"addProjectV2ItemById": {"item": {"id": "I_1"}}}}),
        (200, {"data": {}}),
        (200, {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "I_1", "content": {"number": 12}}
        ]}}}}),
        (200, {"data": {}}),
    ]  # fmt: skip
    wire = Wire(
        {("POST", "/graphql"): graphql, ("GET", f"/repos/{REPO}/issues/12"): (200, {"node_id": "ISSUE_12"})}
    )
    await tracker(wire, project_number=3).set_fields(f"{REPO}#12", {"Cost (€)": 0.42, "Size": "M"})
    ecritures = [
        json.loads(r.content)["variables"]
        for r in wire.requests
        if r.url.path == "/graphql" and "updateProjectV2ItemFieldValue" in json.loads(r.content)["query"]
    ]
    assert [(e["fieldId"], e["value"]) for e in ecritures] == [
        ("F_cost", {"number": 0.42}),
        ("F_size", {"singleSelectOptionId": "O_m"}),
    ]


async def test_a_board_that_cannot_be_found_is_a_configuration_error() -> None:
    wire = Wire({("POST", "/graphql"): (200, {"data": {"organization": {"projectV2": None}}})})
    with pytest.raises(ConfigurationError, match="board Projects v2 #9"):
        await tracker(wire, project_number=9).set_fields(f"{REPO}#12", {"Status": "x"})


def test_the_webhook_signature_is_verified_in_constant_time() -> None:
    t = GitHubTracker(GitHubClient(token="t"), REPO, webhook_secret="s3cret")
    request_bodies = b'{"action": "opened"}'
    bonne = "sha256=" + hmac.new(b"s3cret", request_bodies, hashlib.sha256).hexdigest()
    assert t.verify_webhook({"X-Hub-Signature-256": bonne}, request_bodies)
    assert not t.verify_webhook({"x-hub-signature-256": "sha256=" + "0" * 64}, request_bodies)
    assert not t.verify_webhook({}, request_bodies)
    assert not GitHubTracker(GitHubClient(token="t"), REPO).verify_webhook(
        {"X-Hub-Signature-256": bonne}, request_bodies
    )


def test_commands_in_comments_become_decisions() -> None:
    assert parse_command("merci\n/choregos approve") == {"verb": "approve", "argument": ""}
    assert parse_command("/Choregos reject trop large") == {"verb": "reject", "argument": "trop large"}
    assert parse_command("/choregos fly") is None
    payload = {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": 12},
        "comment": {"body": "/choregos answer oui, en euros", "user": {"login": "marie"}},
        "sender": {"login": "marie"},
    }
    events = parse_github_event("issue_comment", "d-1", payload)
    assert [e.type for e in events] == [InboundEventType.ITEM_COMMENTED, InboundEventType.HUMAN_DECISION]
    assert events[1].payload["verb"] == "answer" and events[1].payload["by"] == "marie"
    assert events[1].work_item_key == f"{REPO}#12" and events[1].project_slug == "billing"


def test_a_closed_pr_is_merged_or_not_and_names_its_ticket_by_its_branch() -> None:
    base = {"repository": {"full_name": REPO}, "sender": {"login": "bot"}}
    pr = {
        "number": 8,
        "head": {"ref": "choregos/12-avoirs", "sha": "s"},
        "base": {"ref": "main"},
        "labels": [],
    }
    fusion = parse_github_event(
        "pull_request", "d", {**base, "action": "closed", "pull_request": {**pr, "merged": True}}
    )
    fermee = parse_github_event(
        "pull_request", "d", {**base, "action": "closed", "pull_request": {**pr, "merged": False}}
    )
    assert fusion[0].type == InboundEventType.PR_MERGED and fermee[0].type == InboundEventType.PR_CLOSED
    assert fusion[0].work_item_key == f"{REPO}#12"
    assert parse_github_event("pull_request", "d", {**base, "action": "edited", "pull_request": pr}) == []
    assert parse_github_event("ping", "d", base) == []


def test_a_card_moved_on_the_board_is_a_move() -> None:
    payload = {
        "repository": {"full_name": REPO},
        "sender": {"login": "marie"},
        "work_item_key": f"{REPO}#12",
        "projects_v2_item": {"project_node_id": "PVT_1", "content_node_id": "I_1"},
        "changes": {
            "field_value": {"field_name": "Status", "from": {"name": "Inbox"}, "to": {"name": "Done"}}
        },
    }
    (event,) = parse_github_event("projects_v2_item", "d", payload)
    assert event.type == InboundEventType.ITEM_MOVED
    assert (event.payload["from_status"], event.payload["to_status"]) == ("Inbox", "Done")


def test_a_completed_check_suite_comes_up_with_its_prs() -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": REPO},
        "check_suite": {
            "conclusion": "failure",
            "head_sha": "s",
            "head_branch": "choregos/12-x",
            "pull_requests": [{"number": 8}],
        },
    }
    (event,) = parse_github_event("check_suite", "d", payload)
    assert event.type == InboundEventType.CHECK_COMPLETED and event.payload["pr_numbers"] == [8]
    assert parse_github_event("check_suite", "d", {**payload, "action": "requested"}) == []


async def test_the_poll_also_searches_the_labels_of_the_start_rules() -> None:
    """S56-03: one search per set of labels; a rule on a type or a state is left to the webhook."""
    wire = Wire(
        {("GET", f"/repos/{REPO}/issues"): [(200, [{"number": 1}]), (200, [{"number": 1}, {"number": 4}])]}
    )
    config = ProjectConfig.model_validate(
        {
            "slug": "billing",
            "org": "varga",
            "start_rules": [{"labels_all": ["bug", "triage"]}, {"item_type": "Incident"}],
        }
    )

    keys = await tracker(wire).list_candidates(config)

    assert keys == [f"{REPO}#1", f"{REPO}#4"]
    assert [r.url.params["labels"] for r in wire.requests] == ["agent-ready", "bug,triage"]


def test_an_opened_issue_carries_its_author_as_reporter() -> None:
    """Who filed the issue reaches the work item (#1179), not only who sent the event."""
    payload = {
        "action": "opened",
        "repository": {"full_name": REPO},
        "issue": {
            "number": 13,
            "title": "Credit notes",
            "user": {"login": "ada"},
            "labels": [],
            "state": "open",
        },
        "sender": {"login": "ada"},
    }
    events = parse_github_event("issues", "d-7", payload)
    assert events[0].type is InboundEventType.ITEM_CREATED
    assert events[0].payload["reporter"] == "ada"
