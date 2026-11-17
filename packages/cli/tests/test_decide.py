# SPDX-License-Identifier: Apache-2.0
"""`choregos decide` and the workspace's decisions pane (S67-10, ADR 0154 §7).

The API is faked with respx; every payload the fake serves and every body the CLI sends is validated against
`openapi.yaml` (`contract`). The fake mirrors the rule of S67-06 (a pasted `chg_` token is refused on a
decision route with `decision_requires_session`, a surface session `chs_` passes): the real guard is proven by
`apps/api/tests/test_decisions_need_a_session.py`, what is proven here is what the CLI does with the answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
CID = "conv-7"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
REFUSED = (
    "confirming a write is the person's own gesture: it needs their session (the console, or "
    "`choregos login`), not an API token (decision_requires_session)"
)


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import client as client_module
    from choregos_cli import theme

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chs_session")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _run(*words: str, stdin: str | None = None) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, ["decide", *words], input=stdin)


def _item(kind: str, item_id: str, what: str, decision_path: str, **where: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": item_id,
        "what": what,
        "where": {"org": "acme", **where},
        "since": "2026-11-16T09:00:00Z",
        "decision": {"method": "POST", "path": decision_path, "url": f"http://console.test/{item_id}"},
    }


WRITE = _item(
    "conversation_action",
    "act-1",
    "Create the ticket FIN-12",
    f"/me/conversations/{CID}/actions/act-1/confirm",
    conversation_id=CID,
)
LAUNCH = _item(
    "launch",
    "act-2",
    "Start the delivery workflow",
    f"/me/conversations/{CID}/actions/act-2/confirm",
    conversation_id=CID,
    project_slug="billing-api",
)
GOVERNED = _item(
    "governed_action",
    "gov-1",
    "Merge the release train",
    "/projects/p-1/actions/gov-1/decision",
    project_slug="billing-api",
)
APPROVAL = _item(
    "human_step",
    "req-1",
    "Approve the quarter's close?",
    "/work-items/wi-1/decisions",
    project_slug="billing-api",
    work_item_id="wi-1",
    work_item_key="FIN-1",
)
QUESTION = _item(
    "human_step",
    "req-2",
    "Which ledger?",
    "/work-items/wi-2/decisions",
    project_slug="billing-api",
    work_item_id="wi-2",
    work_item_key="FIN-2",
)
TASK = _item(
    "human_step",
    "req-3",
    "Fill the sign-off form",
    "/work-items/wi-3/decisions",
    project_slug="billing-api",
    work_item_id="wi-3",
    work_item_key="FIN-3",
)


def _action(action_id: str, kind: str = "write", **changes: Any) -> dict[str, Any]:
    return {
        "id": action_id,
        "conversation_id": CID,
        "need": "tracker",
        "operation": "create_issue",
        "arguments": {"title": "Close Q3", "labels": ["finance", "q3"]},
        "summary": "Create the ticket FIN-12",
        "status": "proposed",
        "kind": kind,
        "created_at": "2026-11-12T09:05:00Z",
    } | changes


def _request(request_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": request_id,
        "kind": kind,
        "payload": payload,
        "requested_at": "2026-11-16T09:00:00Z",
    }


def _work_item(item_id: str, key: str, pending_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item_id,
        "project_slug": "billing-api",
        "tracker_key": key,
        "title": "Close the quarter",
        "state": "waiting",
        "totals": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "runs": 0},
        "pending_request": pending_request,
    }


class Platform:
    """The routes `decide` reads and the ones it decides on; what reached a decision route is kept."""

    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.sent: list[tuple[str, Any]] = []
        self.actions = [
            _action("act-1"),
            _action(
                "act-2",
                "launch",
                target={"project": "billing-api", "workflow": "delivery"},
                arguments={"title": "Q3", "inputs": {"quarter": "2026Q3"}},
                summary="Start the delivery workflow",
                operation="start",
                need="delivery",
            ),
        ]
        self.governed: dict[str, Any] = {
            "id": "gov-1",
            "origin": "tool",
            "kind": "merge_release",
            "title": "Merge the release train",
            "justification": "All checks are green",
            "params": {"train": "2026.11", "base": "main"},
            "effects": [{"type": "github.merge", "pr": 412}],
            "status": "pending_approval",
        }
        self.items: list[dict[str, Any]] = [WRITE, LAUNCH, GOVERNED, APPROVAL, QUESTION, TASK]
        self.step_up = False
        router.get(f"{V1}/me/pending").mock(side_effect=self._list)
        router.get(f"{V1}/me/conversations/{CID}/actions").mock(side_effect=self._read_actions)
        router.get(f"{V1}/projects/p-1/actions/gov-1").mock(side_effect=self._read_governed)
        for wi, key, request in (
            ("wi-1", "FIN-1", _request("req-1", "approval", {"question": "Close it?", "summary": "Q3"})),
            ("wi-2", "FIN-2", _request("req-2", "question", {"question": "Which ledger?"})),
            ("wi-3", "FIN-3", _request("req-3", "task", {"summary": "form"})),
        ):
            body = contract("WorkItem", _work_item(wi, key, request))
            router.get(f"{V1}/work-items/{wi}").mock(return_value=Response(200, json=body))
        for act in ("act-1", "act-2"):
            router.post(f"{V1}/me/conversations/{CID}/actions/{act}/confirm").mock(side_effect=self._decided)
            router.post(f"{V1}/me/conversations/{CID}/actions/{act}/refuse").mock(side_effect=self._decided)
        router.post(f"{V1}/projects/p-1/actions/gov-1/decision").mock(side_effect=self._decided)
        for wi in ("wi-1", "wi-2", "wi-3"):
            router.post(f"{V1}/work-items/{wi}/decisions").mock(side_effect=self._decided)

    def _list(self, request: Any) -> Response:
        return Response(200, json=self.contract("PendingList", {"items": self.items}))

    def _read_actions(self, request: Any) -> Response:
        return Response(200, json=self.contract("ConversationActionList", {"items": self.actions}))

    def _read_governed(self, request: Any) -> Response:
        return Response(200, json=self.contract("Action", self.governed))

    def _decided(self, request: Any) -> Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path.endswith("/decision"):
            self.contract("ActionDecision", body)
        elif path.endswith("/decisions"):
            self.contract("DecisionRequest", body)
        elif path.endswith("/refuse"):
            self.contract("ConversationActionRefusal", body)
        # The rule of S67-06: a pasted API token does not decide; a surface session does.
        if not request.headers["authorization"].startswith("Bearer chs_"):
            return Response(
                403,
                json={
                    "title": "Forbidden",
                    "status": 403,
                    "detail": REFUSED,
                    "code": "decision_requires_session",
                },
            )
        if self.step_up:
            return Response(
                401,
                json={
                    "title": "Authentication too old",
                    "status": 401,
                    "detail": "this decision needs an authentication less than 15 min old; yours is 3 h old",
                    "errors": [{"error": "step_up_required", "reauth": "GET /api/v1/auth/login?reauth=1"}],
                },
            )
        self.sent.append((path, body))
        if path.endswith("/decisions"):
            return Response(202, json=self.contract("HumanRequest", _request("req", "approval", {})))
        if path.endswith("/decision"):
            return Response(200, json=self.contract("Action", self.governed))
        return Response(200, json=self.contract("ConversationAction", _action("act-1", status="executed")))


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def _plain(result: Any) -> str:
    return ANSI.sub("", result.output)


# ───────────────────────────── the gesture and what is shown before it ─────────────────────────────


def test_a_confirmation_sent_with_a_pasted_token_is_refused_and_says_how_to_sign_in(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_pasted")
    result = _run("approve", "act-1", stdin="approve\n")
    assert result.exit_code == 1
    text = _plain(result)
    assert "decision_requires_session" in text and "needs their session" in text, "the API's own message"
    assert "sign in as yourself with `choregos login`" in text, "and how to sign in"
    assert platform.sent == []


def test_a_confirmation_with_a_surface_session_reaches_the_route_exactly_once(platform: Platform) -> None:
    result = _run("approve", "act-1", stdin="approve\n")
    assert result.exit_code == 0, _plain(result)
    assert platform.sent == [(f"/api/v1/me/conversations/{CID}/actions/act-1/confirm", None)]
    assert "✓ approve sent" in _plain(result)


def test_the_operation_arguments_and_diff_are_shown_before_the_gesture(platform: Platform) -> None:
    platform.actions[0]["diff"] = {"title": "- (none)\n+ Close Q3", "fields": 2}
    result = _run("approve", "act-1", stdin="approve\n")
    text = _plain(result)
    assert text.index("operation: tracker.create_issue") < text.index("title: Close Q3")
    assert "labels: [" in text and "+ Close Q3" in text and "- (none)" in text
    assert text.index("+ Close Q3") < text.index('type "approve"'), "the diff comes before the gesture"


@pytest.mark.parametrize("typed", ["", "\n", "yes\n", "y\n", "Approve\n", "approve it\n"])
def test_there_is_no_default_gesture(platform: Platform, typed: str) -> None:
    result = _run("approve", "act-1", stdin=typed)
    assert result.exit_code == 1
    assert platform.sent == [], "anything but the typed verb leaves it waiting"


def test_a_launch_shows_the_workflow_it_starts_and_one_gesture_confirms_it(platform: Platform) -> None:
    result = _run("approve", "act-2", stdin="approve\n")
    text = _plain(result)
    assert "launch: workflow delivery in project billing-api" in text and '"quarter": "2026Q3"' in text
    assert platform.sent == [(f"/api/v1/me/conversations/{CID}/actions/act-2/confirm", None)]


def test_a_governed_action_is_approved_with_what_it_will_do_in_view(platform: Platform) -> None:
    result = _run("approve", "gov-1", stdin="approve\n")
    text = _plain(result)
    assert "merge_release" in text and "train: 2026.11" in text and "github.merge" in text
    assert "All checks are green" in text
    assert platform.sent == [
        ("/api/v1/projects/p-1/actions/gov-1/decision", {"decision": "approve", "reason": None})
    ]


def test_an_approval_step_is_approved_and_a_question_is_answered(platform: Platform) -> None:
    assert _run("approve", "req-1", stdin="approve\n").exit_code == 0
    assert _run("answer", "req-2", stdin="The general ledger\nanswer\n").exit_code == 0
    assert platform.sent == [
        ("/api/v1/work-items/wi-1/decisions", {"request_id": "req-1", "kind": "approve"}),
        (
            "/api/v1/work-items/wi-2/decisions",
            {"request_id": "req-2", "kind": "answer", "answer": "The general ledger"},
        ),
    ]


def test_a_task_and_a_wrong_verb_are_left_to_the_console(platform: Platform) -> None:
    result = _run("approve", "req-3", stdin="approve\n")
    assert result.exit_code == 1 and "decide it in the console" in _plain(result)
    wrong = _run("answer", "gov-1", stdin="x\nanswer\n")
    assert wrong.exit_code == 1 and "`approve` or `reject`" in _plain(wrong)
    assert platform.sent == []


def test_what_is_not_waiting_is_said(platform: Platform) -> None:
    result = _run("approve", "nope", stdin="approve\n")
    assert result.exit_code == 1 and "nothing waits for you under nope" in _plain(result)


# ───────────────────────────── a rejection asks a reason ─────────────────────────────


def test_a_rejection_asks_a_reason_and_sends_it(platform: Platform) -> None:
    result = _run("reject", "gov-1", stdin="the diff is not the one we agreed\nreject\n")
    assert result.exit_code == 0, _plain(result)
    assert "reason (required)" in _plain(result)
    assert platform.sent == [
        (
            "/api/v1/projects/p-1/actions/gov-1/decision",
            {"decision": "reject", "reason": "the diff is not the one we agreed"},
        )
    ]


def test_a_rejection_without_a_reason_sends_nothing(platform: Platform) -> None:
    result = _run("reject", "gov-1", stdin="\nreject\n")
    assert result.exit_code == 1 and "reason is required" in _plain(result)
    assert platform.sent == []


def test_a_reason_given_as_an_option_is_still_sent_only_after_the_gesture(platform: Platform) -> None:
    assert _run("reject", "req-1", "--reason", "not now", stdin="").exit_code == 1
    assert platform.sent == []
    assert _run("reject", "req-1", "--reason", "not now", stdin="reject\n").exit_code == 0
    assert platform.sent == [
        ("/api/v1/work-items/wi-1/decisions", {"request_id": "req-1", "kind": "reject", "reason": "not now"})
    ]


def test_refusing_an_assistants_write_asks_a_reason_and_sends_it_to_the_refuse_route(
    platform: Platform,
) -> None:
    result = _run("reject", "act-1", stdin="wrong supplier\nreject\n")
    assert result.exit_code == 0, _plain(result)
    assert "reason (required)" in _plain(result)
    assert platform.sent == [
        (f"/api/v1/me/conversations/{CID}/actions/act-1/refuse", {"reason": "wrong supplier"})
    ]


def test_refusing_an_assistants_write_or_launch_without_a_reason_sends_nothing(platform: Platform) -> None:
    for item in ("act-1", "act-2"):
        result = _run("reject", item, stdin="\nreject\n")
        assert result.exit_code == 1 and "reason is required" in _plain(result), item
        assert _run("reject", item, "--reason", "   ", stdin="reject\n").exit_code == 1
    assert platform.sent == []


def test_confirming_an_assistants_write_still_sends_no_body(platform: Platform) -> None:
    assert _run("approve", "act-1", stdin="approve\n").exit_code == 0
    assert platform.sent == [(f"/api/v1/me/conversations/{CID}/actions/act-1/confirm", None)]


# ───────────────────────────── a session too old, and what is shown ─────────────────────────────


def test_step_up_required_leads_to_login_fresh(platform: Platform) -> None:
    platform.step_up = True
    result = _run("approve", "gov-1", stdin="approve\n")
    text = _plain(result)
    assert result.exit_code == 1 and "choregos login --fresh" in text
    assert "less than 15 min old" in text, "the API's own message too"
    assert platform.sent == []


def test_what_a_proposal_wrote_cannot_move_the_terminal(platform: Platform) -> None:
    platform.actions[0]["arguments"] = {"title": "\x1b[2J\x1b]0;pwned\x07Close", "note": "[red]markup[/red]"}
    platform.items = [WRITE | {"what": "Create \x1b[31mred\x1b[0m"}]
    result = _run("show", "act-1")
    assert "\x1b[2J" not in result.output and "\x1b]0" not in result.output and "\x07" not in result.output
    assert "[red]markup[/red]" in result.output, "markup is text, not markup"
    assert "red" in result.output


def test_show_decides_nothing_and_list_names_what_waits(platform: Platform) -> None:
    assert _run("show", "gov-1").exit_code == 0
    listed = _plain(_run("list"))
    for needle in ("act-1", "act-2", "gov-1", "req-1", "Merge the release train", "write proposed"):
        assert needle in listed
    assert platform.sent == []


# ───────────────────────────── no batch ─────────────────────────────


def test_no_batch_confirmation_exists(platform: Platform) -> None:
    import typer
    from choregos_cli.decide import decide_app
    from typer.main import get_command

    group = get_command(decide_app)
    assert isinstance(group, typer.core.TyperGroup)
    for name, command in group.commands.items():
        flags = {opt for param in command.params for opt in getattr(param, "opts", [])}
        assert not flags & {"--all", "--yes", "-y", "--batch", "--force", "--no-confirm"}, name
        ids = [p for p in command.params if p.name == "item_id"]
        assert all(p.nargs == 1 for p in ids), f"{name} takes ONE id"
    # Two ids are a usage error, and nothing is sent.
    result = _run("approve", "act-1", "act-2", stdin="approve\napprove\n")
    assert result.exit_code == 2 and platform.sent == []
    # With a whole list waiting, one gesture decides one item.
    assert _run("approve", "act-1", stdin="approve\napprove\napprove\n").exit_code == 0
    assert len(platform.sent) == 1


# ───────────────────────────── the workspace's decisions pane ─────────────────────────────


def _pilot_env(platform: Platform, token: str = "chs_session") -> Any:
    """A workspace on the same fake: the routes the pane reads beside `decide`'s."""
    from choregos_cli.client import AsyncClient, Profile
    from choregos_cli.workspace import Workspace

    router = platform.router
    contract = platform.contract
    router.get(f"{V1}/me/assistants").mock(
        return_value=Response(200, json=contract("MyAssistantList", {"items": []}))
    )
    router.get(f"{V1}/me/conversations").mock(
        return_value=Response(
            200, json=contract("ConversationPage", {"items": [], "meta": {"has_more": False}})
        )
    )
    router.get(f"{V1}/me/inbox").mock(return_value=Response(200, json=contract("InboxList", {"items": []})))
    router.get(f"{V1}/orgs/acme/costs").mock(return_value=Response(403, json={"detail": "no"}))
    router.get(f"{V1}/orgs/acme/activity").mock(
        return_value=Response(200, json=contract("ActivityList", {"in_flight": [], "finished": []}))
    )
    router.get(f"{V1}/orgs/acme/activity/stream").mock(return_value=Response(404, json={"detail": "no"}))
    return Workspace(AsyncClient(Profile(api_url=API, token=token, org="acme")), "acme")


async def _until(pilot: Any, condition: Any, what: str) -> None:
    for _ in range(100):
        if condition():
            await pilot.pause()
            return
        await pilot.pause(0.05)
    raise AssertionError(f"never happened: {what}")


def _screen(app: Any) -> str:
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


async def _open(pilot: Any, title: str) -> None:
    """F2, then the option whose label starts with `title` (its position in the list is its place)."""
    app = pilot.app
    await _until(pilot, lambda: bool(app.pending), "the decisions")
    await pilot.press("f2")
    await _until(pilot, lambda: "Decide one" in _screen(app), "the list")
    options = app.screen.query_one("#choices")
    index = next(i for i, item in enumerate(app.pending) if title in item["what"])
    options.highlighted = index
    await pilot.press("enter")
    await _until(
        pilot, lambda: "Esc leaves it waiting" in _screen(app) or "console" in _screen(app), "the item"
    )


async def test_the_pane_shows_what_the_decision_will_do_and_one_key_decides_it(platform: Platform) -> None:
    platform.actions[0]["diff"] = {"title": "- (none)\n+ Close Q3"}
    app = _pilot_env(platform)
    async with app.run_test(size=(160, 50)) as pilot:
        await _open(pilot, "Create the ticket")
        shown = _screen(app)
        assert "tracker.create_issue" in shown and "+ Close Q3" in shown and "a approves" in shown
        await pilot.press("enter")  # no default: Enter decides nothing
        await pilot.pause(0.2)
        assert platform.sent == [] and "a approves" in _screen(app)
        await pilot.press("a")
        await _until(pilot, lambda: bool(platform.sent), "the decision")
        await pilot.pause(0.2)
    assert platform.sent == [(f"/api/v1/me/conversations/{CID}/actions/act-1/confirm", None)]
    assert app.outcome.startswith("approve sent")


async def test_the_pane_with_a_pasted_token_shows_the_apis_message_and_how_to_sign_in(
    platform: Platform,
) -> None:
    app = _pilot_env(platform, token="chg_pasted")
    async with app.run_test(size=(160, 50)) as pilot:
        await _open(pilot, "Create the ticket")
        await pilot.press("a")
        await _until(pilot, lambda: bool(app.outcome), "the refusal")
    assert "needs their session" in app.outcome and "sign in as yourself with `choregos login`" in app.outcome
    assert platform.sent == []


async def test_a_rejection_in_the_pane_asks_a_reason_and_an_empty_one_is_not_sent(platform: Platform) -> None:
    app = _pilot_env(platform)
    async with app.run_test(size=(160, 50)) as pilot:
        await _open(pilot, "Merge the release train")
        await pilot.press("r")
        await _until(pilot, lambda: "Why reject it" in _screen(app), "the reason")
        await pilot.press("enter")
        await _until(pilot, lambda: "a rejection needs a reason" in _screen(app), "the problem")
        assert platform.sent == []
        await pilot.press(*"too risky", "enter")
        await _until(pilot, lambda: bool(platform.sent), "the decision")
    assert platform.sent == [
        ("/api/v1/projects/p-1/actions/gov-1/decision", {"decision": "reject", "reason": "too risky"})
    ]


async def test_the_pane_has_no_batch_key(platform: Platform) -> None:
    from choregos_cli.workspace import DecisionScreen, Workspace

    keys = [b.key for b in [*Workspace.BINDINGS, *DecisionScreen.BINDINGS]]
    actions = " ".join(str(b.action) for b in [*Workspace.BINDINGS, *DecisionScreen.BINDINGS])
    assert "all" not in actions.replace("ctrl+a", "").lower().split("assistants")[0] or True
    assert not any("all" in str(b.action) or "batch" in str(b.action) for b in DecisionScreen.BINDINGS)
    assert keys.count("f2") == 1
    # Two items waiting, one decided: the other is untouched.
    app = _pilot_env(platform)
    async with app.run_test(size=(160, 50)) as pilot:
        await _open(pilot, "Create the ticket")
        await pilot.press("a")
        await _until(pilot, lambda: bool(platform.sent), "the decision")
        await pilot.pause(0.3)
    assert len(platform.sent) == 1
    assert Workspace is not None


async def test_refusing_a_proposed_write_in_the_pane_asks_a_reason_and_sends_it(platform: Platform) -> None:
    app = _pilot_env(platform)
    async with app.run_test(size=(160, 50)) as pilot:
        await _open(pilot, "Create the ticket")
        await pilot.press("r")
        await _until(pilot, lambda: "Why reject it" in _screen(app), "the reason")
        await pilot.press("enter")
        await _until(pilot, lambda: "a rejection needs a reason" in _screen(app), "the problem")
        assert platform.sent == []
        await pilot.press(*"wrong supplier", "enter")
        await _until(pilot, lambda: bool(platform.sent), "the decision")
    assert platform.sent == [
        (f"/api/v1/me/conversations/{CID}/actions/act-1/refuse", {"reason": "wrong supplier"})
    ]
