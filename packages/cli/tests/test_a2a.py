"""`choregos a2a` (S62-03): start a workflow over the A2A door, hand it data and files, follow it.

The door is mocked here with respx, call by call; `apps/api/tests/test_a2a_cli.py` drives the real one.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
DOOR = f"{API}/a2a/orgs/acme"
CARD = f"{DOOR}/.well-known/agent-card.json"
CONSOLE = "https://choregos.example.com/p/billing-api/items/t-1"
FIELDS_EXTENSION = "https://choregos.dev/a2a/extensions/skill-fields/v1"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_url": API, "token": "chg_profile", "org": "acme"}))
    import choregos_cli.a2a as a2a_module
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)
    monkeypatch.setattr(a2a_module, "RECONNECT_PAUSE", 0.0)
    monkeypatch.delenv("CHOREGOS_A2A_TOKEN", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    return path


def _cli(*args: str, env: dict[str, str] | None = None) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, ["a2a", *args], env=env)


def task(state: str, *, workflow_state: str = "inbox", parts: list[Any] | None = None, **extra: Any) -> Any:
    return {
        "kind": "task",
        "id": "t-1",
        "contextId": "t-1",
        "status": {"state": state, "message": {"kind": "message", "role": "agent", "parts": parts or []}},
        "artifacts": [],
        "metadata": {
            "choregos": {
                "work_item": "t-1",
                "key": "BIL-12",
                "project": "acme:billing-api",
                "state": workflow_state,
                "console_url": CONSOLE,
            }
        },
        **extra,
    }


def status(state: str, *, final: bool = False, **task_args: Any) -> Any:
    whole = task(state, **task_args)
    return {
        "kind": "status-update",
        "taskId": "t-1",
        "contextId": "t-1",
        "status": whole["status"],
        "final": final,
        "metadata": whole["metadata"],
    }


def waiting(request_id: str = "r-1") -> list[Any]:
    return [
        {"kind": "text", "text": f"Waiting for a person's approval: {CONSOLE}"},
        {"kind": "data", "data": {"decision_url": CONSOLE, "kind": "approval", "request_id": request_id}},
    ]


def answer(body: Any, result: Any) -> Response:
    return Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def sse(body: Any, *results: Any) -> Response:
    frames = "".join(
        f"data: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': r})}\r\n\r\n" for r in results
    )
    return Response(200, headers={"content-type": "text/event-stream"}, content=(": ping\r\n\r\n" + frames))


class Door:
    """A scripted door: each method answers from its own queue, and every call is kept."""

    def __init__(self, **answers: list[Callable[[Any], Response]]) -> None:
        self.answers = answers
        self.calls: list[Any] = []

    def __call__(self, request: httpx.Request) -> Response:
        body = json.loads(request.content)
        self.calls.append({"body": body, "headers": dict(request.headers)})
        return self.answers[body["method"]].pop(0)(body)

    def methods(self) -> list[str]:
        return [call["body"]["method"] for call in self.calls]


def _door(**answers: list[Callable[[Any], Response]]) -> Door:
    door = Door(**answers)
    respx.post(DOOR).mock(side_effect=door)
    return door


@respx.mock
def test_skills_list_each_skill_with_the_fields_of_the_card_s_extension() -> None:
    card = {
        "skills": [
            {"id": "billing-api/ship-a-change", "description": "Ship it", "inputModes": ["application/json"]},
            {"id": "billing-api/notes", "description": "Notes", "inputModes": ["text/plain"]},
        ],
        "capabilities": {
            "extensions": [
                {
                    "uri": FIELDS_EXTENSION,
                    "params": {
                        "skills": {
                            "billing-api/ship-a-change": {
                                "type": "object",
                                "required": ["ticket"],
                                "properties": {
                                    "ticket": {"type": "string"},
                                    "priority": {"type": "string", "enum": ["low", "high"]},
                                },
                            }
                        }
                    },
                }
            ]
        },
    }
    respx.get(CARD).mock(return_value=Response(200, json=card))
    shown = _cli("skills")
    assert shown.exit_code == 0, shown.output
    assert "ticket* (string), priority (low|high)" in shown.stdout
    assert "billing-api/notes" in shown.stdout
    as_json = _cli("skills", "--json")
    skills = json.loads(as_json.stdout)
    assert skills[0]["fields"]["required"] == ["ticket"]
    assert skills[1]["fields"] is None


@respx.mock
def test_send_puts_text_fields_and_files_in_one_message(tmp_path: Path) -> None:
    door = _door(**{"message/send": [lambda b: answer(b, task("working"))] * 3})
    spec = tmp_path / "spec.pdf"
    spec.write_bytes(b"%PDF-1.7\n")
    notes = tmp_path / "notes.bin"
    notes.write_bytes(b"\x00\x01")
    fields = tmp_path / "fields.json"
    fields.write_text(json.dumps({"ticket": "FIN-41", "priority": "low"}))
    args = [
        "send", "billing-api/ship-a-change", "--title", "Add credit notes",
        "--text", "Customers need a credit note.", "--fields-json", str(fields),
        "--field", "ticket=FIN-42", "--field", "estimate=3",
        "--file", str(spec), "--file", f"{notes};type=application/x-notes",
    ]  # fmt: skip
    sent = _cli(*args)
    assert sent.exit_code == 0, sent.output
    message = door.calls[0]["body"]["params"]["message"]
    assert message["metadata"] == {"skill": "billing-api/ship-a-change", "title": "Add credit notes"}
    assert message["parts"] == [
        {"kind": "text", "text": "Customers need a credit note."},
        {"kind": "data", "data": {"ticket": "FIN-42", "priority": "low", "estimate": 3}},
        {
            "kind": "file",
            "file": {
                "name": "spec.pdf",
                "mimeType": "application/pdf",
                "bytes": base64.b64encode(b"%PDF-1.7\n").decode(),
            },
        },
        {"kind": "file", "file": {"name": "notes.bin", "mimeType": "application/x-notes", "bytes": "AAE="}},
    ]
    # The message id is printed, and the same command sends the same one: a retry finds its task.
    assert f"message id: {message['messageId']}" in sent.stderr
    assert message["messageId"].startswith("cli-")
    assert _cli(*args).exit_code == 0
    assert door.calls[1]["body"]["params"]["message"]["messageId"] == message["messageId"]
    other = _cli(*[a if a != "Customers need a credit note." else "Something else." for a in args])
    assert other.exit_code == 0
    assert door.calls[2]["body"]["params"]["message"]["messageId"] != message["messageId"]
    assert "task t-1 — working, BIL-12" in sent.stdout
    assert CONSOLE in sent.stdout


@respx.mock
def test_an_explicit_message_id_is_sent_as_given() -> None:
    door = _door(**{"message/send": [lambda b: answer(b, task("working"))]})
    sent = _cli("send", "ship-a-change", "--text", "hello", "--message-id", "order-2026-0042")
    assert sent.exit_code == 0, sent.output
    assert door.calls[0]["body"]["params"]["message"]["messageId"] == "order-2026-0042"


def test_send_refuses_an_empty_message_and_a_malformed_field() -> None:
    assert _cli("send", "ship-a-change").exit_code == 2
    refused = _cli("send", "ship-a-change", "--field", "no-equals")
    assert refused.exit_code == 2
    assert "key=value" in refused.stderr


@respx.mock
def test_send_wait_follows_through_a_decision_and_the_hour_cut_then_writes_the_artefacts(
    tmp_path: Path,
) -> None:
    completed = task(
        "completed",
        workflow_state="done",
        artifacts=[
            {"artifactId": "report@1", "name": "report", "parts": [{"kind": "text", "text": "# Done"}]},
            {"artifactId": "facts@1", "name": "facts", "parts": [{"kind": "data", "data": {"total": 3}}]},
            {
                "artifactId": "invoice.pdf@2",
                "name": "invoice.pdf",
                "parts": [
                    {
                        "kind": "file",
                        "file": {"name": "invoice.pdf", "mimeType": "application/pdf", "bytes": "JVBERg=="},
                    }
                ],
            },
            {
                "artifactId": "big@1",
                "name": "big",
                "parts": [{"kind": "data", "data": {"name": "big"}}],
                "metadata": {"choregos": {"content": "not in the task: read it in the console"}},
            },
        ],
    )
    door = _door(
        **{
            "message/send": [lambda b: answer(b, task("working"))],
            # The first stream is cut (the door's hour) while a person must decide; the second ends it.
            "tasks/resubscribe": [
                lambda b: sse(b, task("working"), status("input-required", parts=waiting())),
                lambda b: sse(b, task("input-required", parts=waiting()), status("completed", final=True)),
            ],
            "tasks/get": [lambda b: answer(b, completed)],
        }
    )
    out = tmp_path / "out"
    sent = _cli("send", "ship-a-change", "--text", "go", "--wait", "--out", str(out))
    assert sent.exit_code == 0, sent.output
    assert door.methods() == ["message/send", "tasks/resubscribe", "tasks/resubscribe", "tasks/get"]
    assert door.calls[1]["body"]["params"] == {"id": "t-1"}
    assert sent.stdout.count(f"in the console: {CONSOLE}") == 1, sent.stdout
    assert (out / "report.md").read_text() == "# Done"
    assert json.loads((out / "facts.json").read_text()) == {"total": 3}
    assert (out / "invoice.pdf").read_bytes() == b"%PDF"
    assert not (out / "big.json").exists()
    assert "big not written: not in the task" in sent.stdout


@respx.mock
@pytest.mark.parametrize(("state", "code"), [("failed", 3), ("canceled", 4), ("completed", 0)])
def test_follow_exits_on_the_final_state(state: str, code: int) -> None:
    _door(
        **{
            "tasks/resubscribe": [lambda b: sse(b, task("working"), status(state, final=True))],
            "tasks/get": [lambda b: answer(b, task(state))],
        }
    )
    followed = _cli("follow", "t-1")
    assert followed.exit_code == code, followed.output


@respx.mock
def test_send_wait_json_prints_only_the_final_task_on_stdout() -> None:
    final = task("failed")
    _door(
        **{
            "message/send": [lambda b: answer(b, task("working"))],
            "tasks/resubscribe": [lambda b: sse(b, task("working"), status("failed", final=True))],
            "tasks/get": [lambda b: answer(b, final)],
        }
    )
    sent = _cli("send", "ship-a-change", "--text", "go", "--wait", "--json")
    assert sent.exit_code == 3, sent.output
    assert json.loads(sent.stdout) == final
    assert "message id: cli-" in sent.stderr


@respx.mock
def test_a_stream_that_breaks_is_opened_again() -> None:
    def broken(body: Any) -> Response:
        first = {"jsonrpc": "2.0", "id": body["id"], "result": task("working")}

        class Breaks(httpx.AsyncByteStream):
            async def __aiter__(self):  # type: ignore[no-untyped-def]
                yield f"data: {json.dumps(first)}\n\n".encode()
                raise httpx.ReadError("connection reset")

        return Response(200, headers={"content-type": "text/event-stream"}, stream=Breaks())

    door = _door(
        **{
            "tasks/resubscribe": [broken, lambda b: sse(b, status("completed", final=True))],
            "tasks/get": [lambda b: answer(b, task("completed"))],
        }
    )
    followed = _cli("follow", "t-1")
    assert followed.exit_code == 0, followed.output
    assert door.methods() == ["tasks/resubscribe", "tasks/resubscribe", "tasks/get"]


@respx.mock
def test_an_unreachable_door_is_a_refusal_not_an_endless_retry() -> None:
    route = respx.post(DOOR).mock(side_effect=httpx.ConnectError("refused"))
    followed = _cli("follow", "t-1")
    assert followed.exit_code == 1, followed.output
    assert "cannot reach" in followed.stderr
    assert route.call_count == 1


@respx.mock
def test_a_star_token_is_refused_and_the_cli_says_which_token_opens_the_door() -> None:
    door = _door(
        **{
            "tasks/get": [
                lambda b: Response(
                    403,
                    json={"error": "the A2A door refuses a token without its scope (`*` included)"},
                    headers={"WWW-Authenticate": 'Bearer realm="choregos", error="insufficient_scope"'},
                )
            ]
        }
    )
    read = _cli("get", "t-1")
    assert read.exit_code == 1, read.output
    assert "HTTP 403" in read.stderr
    assert "--scope a2a" in read.stderr and "choregos machines token" in read.stderr
    # Without --token, the profile's token is sent.
    assert door.calls[0]["headers"]["authorization"] == "Bearer chg_profile"


@respx.mock
def test_the_a2a_token_comes_from_the_option_then_the_environment() -> None:
    door = _door(**{"tasks/get": [lambda b: answer(b, task("working"))] * 2})
    assert _cli("get", "t-1", "--token", "chg_option").exit_code == 0
    assert _cli("get", "t-1", env={"CHOREGOS_A2A_TOKEN": "chg_env"}).exit_code == 0
    assert [c["headers"]["authorization"] for c in door.calls] == ["Bearer chg_option", "Bearer chg_env"]


@respx.mock
def test_refused_fields_are_listed_one_by_one() -> None:
    def refusal(body: Any) -> Response:
        error = {
            "code": -32602,
            "message": "fields refused by workflow `ship-a-change`",
            "data": {"errors": [{"loc": ["fields"], "msg": "'ticket' is a required property"}]},
        }
        return Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})

    _door(**{"message/send": [refusal]})
    sent = _cli("send", "ship-a-change", "--field", "priority=high")
    assert sent.exit_code == 1, sent.output
    assert "JSON-RPC -32602: fields refused by workflow" in sent.stderr
    assert "  - fields: 'ticket' is a required property" in sent.stderr


@respx.mock
def test_get_json_is_the_task_as_the_door_answers_it() -> None:
    read = task("input-required", parts=waiting())
    _door(**{"tasks/get": [lambda b: answer(b, read)] * 2})
    as_json = _cli("get", "t-1", "--json")
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout) == read
    shown = _cli("get", "t-1")
    assert f"a person must decide, in the console: {CONSOLE}" in shown.stdout


@respx.mock
def test_cancel_stops_the_task() -> None:
    door = _door(**{"tasks/cancel": [lambda b: answer(b, task("working", workflow_state="stopping"))]})
    stopped = _cli("cancel", "t-1")
    assert stopped.exit_code == 0, stopped.output
    assert door.calls[0]["body"]["params"] == {"id": "t-1"}
    assert "stop sent to task t-1" in stopped.stdout
