# SPDX-License-Identifier: Apache-2.0
"""The public page `docs/site/integrate/a2a.md` says what the A2A door does (S62-09b).

The page's opt-in block is published, the application's account is created by running the page's own
`choregos machines` commands against the API, then every `curl` request of the page is sent to the
door in the page's order, with the headers the page shows. A request the door stops taking, an answer
of another shape, or a refusal code the page does not list turns this red.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from typing import Any

import yaml
from httpx import AsyncClient

from .site_pages import run_cli

PAGE = Path(__file__).resolve().parents[3] / "docs" / "site" / "integrate" / "a2a.md"
ENDPOINT = "/a2a/orgs/varga"
CARD = f"{ENDPOINT}/.well-known/agent-card.json"
#: A `curl` call of the page to the door: from `curl` to the body's closing `}'`.
CALL = re.compile(r"^curl [^\n]*\"\$A2A\"[^\n]*-d '(\{.*?^\})'", re.M | re.S)
HEADER = re.compile(r"-H '([^':]+): ([^']+)'")
OPT_IN = re.compile(r"```yaml\n(metadata:.*?a2a:.*?)```", re.S)
ANSWER = re.compile(r"```json\n(.*?)```", re.S)
BASH = re.compile(r"```(?:bash|shell)\n(.*?)```", re.S)

METHODS = [
    "message/send",
    "message/send",
    "tasks/get",
    "tasks/resubscribe",
    "tasks/pushNotificationConfig/set",
    "tasks/cancel",
    "SendMessage",
    "GetTask",
    "SubscribeToTask",
    "CreateTaskPushNotificationConfig",
    "CancelTask",
]


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _calls() -> list[tuple[dict[str, str], dict[str, Any]]]:
    calls = []
    for match in CALL.finditer(_page()):
        line = match.group(0).split("\n", 1)[0]
        headers = {k: v for k, v in HEADER.findall(line) if k not in {"Content-Type"}}
        calls.append((headers, json.loads(match.group(1))))
    assert calls, "the page has no request any more, or its shape changed without this test"
    return calls


def _commands(prefix: str) -> list[list[str]]:
    found = []
    for block in BASH.findall(_page()):
        for line in block.replace("\\\n", " ").splitlines():
            if line.startswith(prefix):
                found.append(shlex.split(line))
    return found


async def _publish(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_core.dsl import template_yaml

    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    block = OPT_IN.search(_page())
    assert block, "the page no longer shows the workflow's opt-in"
    opt_in = yaml.safe_load(block.group(1))
    workflow = yaml.safe_load(template_yaml("default-simple"))
    workflow["metadata"]["inputs"] = opt_in["metadata"]["inputs"]
    workflow["metadata"]["name"] = opt_in["metadata"]["name"]
    workflow["a2a"] = opt_in["a2a"]
    name = opt_in["metadata"]["name"]
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/{name}",
        json={"yaml": yaml.safe_dump(workflow, sort_keys=False)},
    )
    assert published.status_code in {200, 201}, published.text


async def _application_token(client: AsyncClient) -> str:
    """The page's two `choregos machines` commands, run against the API as the project's admin."""
    create = next(c for c in _commands("choregos machines create"))
    token = next(c for c in _commands("choregos machines token"))
    created = await run_cli(client, create)
    assert created.exit_code == 0, created.output
    issued = await run_cli(client, token)
    assert issued.exit_code == 0, issued.output
    secret = issued.output.strip().splitlines()[-1].strip()
    assert secret.startswith("chg_"), issued.output
    client.cookies.clear()
    return secret


async def _post(client: AsyncClient, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    answer = await client.post(ENDPOINT, json=body, headers=headers)
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


async def _frames(client: AsyncClient, body: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    async with asyncio.timeout(10), client.stream("POST", ENDPOINT, json=body, headers=headers) as answer:
        assert answer.status_code == 200
        async for line in answer.aiter_lines():
            if line.startswith("data:"):
                frames.append(json.loads(line.removeprefix("data:")))
    return frames


def _with_task(body: dict[str, Any], task: str) -> dict[str, Any]:
    return dict(json.loads(json.dumps(body).replace("<task id>", task)))


def _same_shape(sample: Any, real: Any, path: str = "result") -> None:
    assert isinstance(real, type(sample)), f"{path}: the page shows {sample!r}, the door {real!r}"
    if isinstance(sample, dict):
        for key, value in sample.items():
            assert key in real, f"{path}.{key}: shown by the page, absent from the door's answer"
            _same_shape(value, real[key], f"{path}.{key}")


async def _fail(task: str) -> None:
    """The work item reaches a final state, so that a stream on it comes to its end."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, task)
        assert item is not None
        item.failure = {"reason": "boom"}


async def test_the_page_s_requests_work_in_its_order(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import get_temporal

    await _publish(client, project)
    token = await _application_token(client)
    auth = {"Authorization": f"Bearer {token}"}

    card = (await client.get(CARD, headers=auth)).json()
    assert [skill["id"] for skill in card["skills"]] == ["billing-api/ship-a-change"], card
    fields = next(e for e in card["capabilities"]["extensions"] if e["uri"].endswith("/skill-fields/v1"))
    assert fields["uri"] in _page()
    assert fields["params"]["skills"]["billing-api/ship-a-change"]["required"] == ["ticket"]

    calls = _calls()
    assert [body["method"] for _, body in calls] == METHODS
    (start_h, start), (file_h, with_file), (get_h, get), (follow_h, follow), (push_h, push) = calls[:5]
    cancel_h, cancel = calls[5]

    first = await _post(client, start, {**auth, **start_h})
    assert "result" in first, first
    sample = json.loads(ANSWER.search(_page()).group(1))  # type: ignore[union-attr]
    _same_shape(sample["result"], first["result"])
    assert first["result"]["status"]["state"] == sample["result"]["status"]["state"]
    task = first["result"]["id"]
    again = await _post(client, start, {**auth, **start_h})
    assert again["result"]["id"] == task, "the page promises the same work item for the same messageId"

    filed = await _post(client, with_file, {**auth, **file_h})
    assert filed["result"]["id"] != task, filed
    assert (await _post(client, _with_task(get, task), {**auth, **get_h}))["result"]["id"] == task

    await _fail(filed["result"]["id"])
    frames = await _frames(client, _with_task(follow, filed["result"]["id"]), {**auth, **follow_h})
    assert frames[-1]["result"]["kind"] == "status-update" and frames[-1]["result"]["final"] is True, frames

    registered = await _post(client, _with_task(push, task), {**auth, **push_h})
    assert registered["result"]["signing"]["secret"], registered
    assert registered["result"]["signing"]["header"] == "X-Choregos-Signature"
    assert registered["result"]["signing"]["header"] in _page()

    stopped = await _post(client, _with_task(cancel, task), {**auth, **cancel_h})
    assert "result" in stopped, stopped
    controls = [s[2] for s in get_temporal().signals if s[1] == "control"]  # type: ignore[attr-defined]
    assert [c["action"] for c in controls] == ["stop"]

    # A2A 1.0: a new task (file included), read; the failed task followed; a push; a cancel.
    (send1_h, send1), (get1_h, get1), (sub1_h, sub1), (push1_h, push1), (cancel1_h, cancel1) = calls[6:]
    for headers in (send1_h, get1_h, sub1_h, push1_h, cancel1_h):
        assert headers == {"A2A-Version": "1.0"}, headers
    opened = await _post(client, send1, {**auth, **send1_h})
    assert opened["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING", opened
    task1 = opened["result"]["task"]["id"]
    assert task1 not in {task, filed["result"]["id"]}
    read = await _post(client, _with_task(get1, task1), {**auth, **get1_h})
    assert read["result"]["id"] == task1, read
    events = await _frames(client, _with_task(sub1, filed["result"]["id"]), {**auth, **sub1_h})
    last = events[-1]["result"]
    status = (last.get("statusUpdate") or last.get("task") or {}).get("status", {})
    assert status.get("state") == "TASK_STATE_FAILED", events
    assert all("final" not in json.dumps(e) for e in events), "1.0 streams carry no `final`"
    created = await _post(client, _with_task(push1, task1), {**auth, **push1_h})
    assert created["result"]["url"] == "https://hooks.partner.example/a2a", created
    canceled = await _post(client, _with_task(cancel1, task1), {**auth, **cancel1_h})
    assert "result" in canceled, canceled


async def test_a_file_part_is_stored_as_an_input_of_the_work_item(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _publish(client, project)
    token = await _application_token(client)
    headers, body = _calls()[1]
    filed = await _post(client, body, {"Authorization": f"Bearer {token}", **headers})
    async with session_scope() as session:
        names = (
            await session.execute(
                select(ArtifactRecord.name).where(ArtifactRecord.work_item_id == filed["result"]["id"])
            )
        ).scalars()
        assert "spec.pdf" in set(names)


async def test_the_refusals_the_page_lists_are_the_door_s(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Fields the workflow refuses, a version the door does not speak, a full-access token."""
    await _publish(client, project)
    full = await client.post("/api/v1/me/tokens", json={"name": "full", "scopes": ["*"]})
    token = await _application_token(client)
    auth = {"Authorization": f"Bearer {token}"}
    body = _calls()[0][1]
    body["params"]["message"]["messageId"] = "no-ticket"
    body["params"]["message"]["parts"][1]["data"] = {"priority": "urgent"}
    refused = await _post(client, body, auth)
    assert refused["error"]["code"] == -32602 and "fields refused by workflow" in refused["error"]["message"]
    version = await _post(client, _calls()[0][1], {**auth, "A2A-Version": "2.0"})
    assert version["error"]["code"] == -32009, version
    scope = await client.get(CARD, headers={"Authorization": f"Bearer {full.json()['token']}"})
    assert scope.status_code == 403 and "insufficient_scope" in scope.headers["www-authenticate"]
    table = _page().split("## When it refuses", 1)[1]
    for code in ("-32602", "-32009", "`403`, `insufficient_scope`"):
        assert code in table, code
