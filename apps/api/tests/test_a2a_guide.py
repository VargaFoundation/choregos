# SPDX-License-Identifier: Apache-2.0
"""The guide `docs/starting-a-workflow-from-outside.md` says what the A2A door does.

Its opt-in and each of its `curl` bodies are read from the page and sent to the door, in the page's
order, so that a request the door no longer takes, or an answer of another shape, turns this red.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
from typing import Any

import yaml
from httpx import AsyncClient

GUIDE = pathlib.Path(__file__).resolve().parents[3] / "docs" / "starting-a-workflow-from-outside.md"
ENDPOINT = "/a2a/orgs/varga"
CARD = f"{ENDPOINT}/.well-known/agent-card.json"
#: A `curl` body of the guide: `-d '{ … }'`, closed at the start of a line.
BODY = re.compile(r"\"\$A2A\".*?-d '(\{.*?^\})'", re.M | re.S)
#: The opt-in block and the sample answer, the page's only `yaml` block with `a2a:` and `json` block.
OPT_IN = re.compile(r"```yaml\n(metadata:.*?a2a:.*?)```", re.S)
ANSWER = re.compile(r"```json\n(.*?)```", re.S)


def _page() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _bodies() -> list[dict[str, Any]]:
    found = [json.loads(text) for text in BODY.findall(_page())]
    assert found, "the guide has no request any more, or its shape changed without this test"
    return found


async def _publish(
    client: AsyncClient, project: dict[str, Any], *, input_modes: list[str] | None = None
) -> None:
    from choregos_core.dsl import template_yaml

    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    block = OPT_IN.search(_page())
    assert block, "the guide no longer shows the workflow's opt-in"
    opt_in = yaml.safe_load(block.group(1))
    workflow = yaml.safe_load(template_yaml("default-simple"))
    workflow["metadata"]["inputs"] = opt_in["metadata"]["inputs"]
    workflow["a2a"] = opt_in["a2a"]
    if input_modes:
        workflow["a2a"]["input_modes"] = input_modes
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": yaml.safe_dump(workflow, sort_keys=False)}
    )
    assert published.status_code == 200, published.text


async def _token(client: AsyncClient) -> str:
    created = await client.post("/api/v1/me/tokens", json={"name": "partner-app", "scopes": ["a2a"]})
    assert created.status_code == 201, created.text
    client.cookies.clear()
    return str(created.json()["token"])


async def _frames(client: AsyncClient, body: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
    """The JSON-RPC answers of one server-sent stream; a stream that does not end fails the test."""
    frames: list[dict[str, Any]] = []
    async with asyncio.timeout(10), client.stream("POST", ENDPOINT, json=body, headers=headers) as answer:
        assert answer.status_code == 200
        async for line in answer.aiter_lines():
            if line.startswith("data:"):
                frames.append(json.loads(line.removeprefix("data:")))
    return frames


def _same_shape(sample: Any, real: Any, path: str = "result") -> None:
    """Every key the guide shows is in the real answer, with a value of the same type."""
    assert isinstance(real, type(sample)), f"{path}: the guide shows {sample!r}, the door {real!r}"
    if isinstance(sample, dict):
        for key, value in sample.items():
            assert key in real, f"{path}.{key}: shown by the guide, absent from the door's answer"
            _same_shape(value, real[key], f"{path}.{key}")


async def test_the_guide_s_requests_work_in_its_order(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal

    await _publish(client, project)
    token = await _token(client)
    headers = {"Authorization": f"Bearer {token}"}

    card = (await client.get(CARD, headers=headers)).json()
    assert [skill["id"] for skill in card["skills"]] == ["billing-api/ship-a-change"]

    bodies = _bodies()
    methods = [body["method"] for body in bodies]
    assert methods == ["message/send", "message/send", "tasks/get", "tasks/resubscribe", "tasks/cancel"]

    first = await client.post(ENDPOINT, json=bodies[0], headers=headers)
    answer = first.json()
    assert "result" in answer, first.text
    sample = json.loads(ANSWER.search(_page()).group(1))  # type: ignore[union-attr]
    _same_shape(sample["result"], answer["result"])
    assert answer["result"]["status"]["state"] == sample["result"]["status"]["state"]
    task = answer["result"]["id"]

    # A timeout, then the same message again: the guide promises the same work item.
    again = (await client.post(ENDPOINT, json=bodies[0], headers=headers)).json()
    assert again["result"]["id"] == task

    with_file = (await client.post(ENDPOINT, json=bodies[1], headers=headers)).json()
    assert with_file["result"]["id"] != task, with_file

    read = json.loads(json.dumps(bodies[2]).replace("<task id>", task))
    assert (await client.post(ENDPOINT, json=read, headers=headers)).json()["result"]["id"] == task

    # A stream runs until a final status: the second task has one, so the guide's call comes back.
    async with session_scope() as session:
        finished = await session.get(WorkItem, with_file["result"]["id"])
        assert finished is not None
        finished.failure = {"reason": "boom"}
    follow = json.loads(json.dumps(bodies[3]).replace("<task id>", with_file["result"]["id"]))
    frames = await _frames(client, follow, headers)
    assert frames[-1]["result"]["kind"] == "status-update" and frames[-1]["result"]["final"] is True, frames

    cancel = json.loads(json.dumps(bodies[4]).replace("<task id>", task))
    stopped = (await client.post(ENDPOINT, json=cancel, headers=headers)).json()
    assert "result" in stopped, stopped
    controls = [s[2] for s in get_temporal().signals if s[1] == "control"]  # type: ignore[attr-defined]
    assert [c["action"] for c in controls] == ["stop"]


async def test_the_fields_the_guide_declares_are_enforced(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The guide's table: fields `metadata.inputs` refuses come back as `-32602`."""
    await _publish(client, project)
    token = await _token(client)
    body = _bodies()[0]
    body["params"]["message"]["messageId"] = "no-ticket"
    body["params"]["message"]["parts"][1]["data"] = {"priority": "high"}
    refused = (await client.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {token}"})).json()
    assert refused["error"]["code"] == -32602, refused
    assert refused["error"]["message"].startswith("fields refused by workflow"), refused
    assert "'ticket' is a required property" in [e["msg"] for e in refused["error"]["data"]["errors"]], (
        refused
    )


async def test_the_card_says_which_fields_a_skill_takes(client: AsyncClient, project: dict[str, Any]) -> None:
    """A2A's skill has no input schema: the guide points at the card's extension, which carries it."""
    from choregos_adapters.a2a import parse_card
    from choregos_api.a2a.door import SKILL_FIELDS_EXTENSION

    assert SKILL_FIELDS_EXTENSION in _page()
    await _publish(client, project)
    token = await _token(client)
    raw = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert parse_card(raw).skills, "a client that ignores the extension must still read the card"
    extension = raw["capabilities"]["extensions"][0]
    assert extension["uri"] == SKILL_FIELDS_EXTENSION and extension["required"] is False
    schema = extension["params"]["skills"]["billing-api/ship-a-change"]
    assert schema["required"] == ["ticket"] and set(schema["properties"]) == {"ticket", "priority"}


async def test_a_skill_that_takes_no_data_part_publishes_no_fields(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _publish(client, project, input_modes=["text/plain"])
    token = await _token(client)
    raw = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert "extensions" not in raw["capabilities"], raw["capabilities"]


async def test_the_guide_s_first_request_in_a2a_1_0_opens_a_task(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The page's first request, spelled as its "In A2A 1.0" table says, is served."""
    first = _bodies()[0]
    assert first["method"] == "message/send", first
    message = dict(first["params"]["message"])
    message.pop("kind", None)
    message["role"] = "ROLE_USER"
    message["parts"] = [{k: v for k, v in part.items() if k != "kind"} for part in message["parts"]]
    assert all(set(part) <= {"text", "data"} for part in message["parts"]), (
        "the guide's first request sends no file"
    )
    await _publish(client, project)
    token = await _token(client)
    body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}}
    answer = (await client.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {token}"})).json()
    assert answer["result"]["task"]["status"]["state"].startswith("TASK_STATE_"), answer


def test_the_1_0_names_the_guide_lists_are_the_door_s() -> None:
    from choregos_api.a2a import v1

    section = _page().split("### In A2A 1.0", 1)[1].split("\n## ", 1)[0]
    names = set(re.findall(r"`([A-Z][A-Za-z]+)`", section))
    assert {"SendMessage", "GetTask", "ListTasks"} <= names, names
    assert names <= set(v1.METHODS) | v1.UNSERVED, names - set(v1.METHODS) - v1.UNSERVED
    assert (
        set(v1.METHODS)
        - {
            "GetTaskPushNotificationConfig",
            "ListTaskPushNotificationConfigs",
            "DeleteTaskPushNotificationConfig",
        }
        <= names
    )
