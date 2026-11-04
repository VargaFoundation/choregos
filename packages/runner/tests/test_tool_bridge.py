"""The runner serves the tool bridge on its own tool server, when the run is offered scripts (ADR 0122).

A script the agent writes reaches its run's tools on the port the agent's MCP client already uses,
without a credential of its own: the runner's server relays with the runner's client, journals
what it relayed in the runner's journal, and the confinement opens no new port for it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from choregos_contracts import StageInput, ToolScripts
from choregos_runner.config import RunnerSettings
from choregos_runner.exits import Exit
from choregos_runner.runner import EventJournal, Runner
from choregos_runner.workspace import agent_home

from .conftest import FakeInternalClient, agent_script, port_of, valid_result

#: A run token as long as a real one, held by the runner and its environment, never by the agent.
TOKEN = "rt-runner-5e1d2c3b4a59687766554433"
SCRIPT = "from choregos_tools import crm\nprint(len(crm.list_contacts(q='acme')))\n"
DIGEST = hashlib.sha256(SCRIPT.encode()).hexdigest()


@pytest.fixture(autouse=True)
def own_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's `$HOME` is under the temporary directory, at a fixed path: each test has its own."""
    own = tmp_path / "tmp"
    own.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(own))


@pytest.fixture
def settings(runner_settings: RunnerSettings, monkeypatch: pytest.MonkeyPatch) -> RunnerSettings:
    """The runner as an executor starts it: the run token in its settings and in its environment."""
    monkeypatch.setenv("CHOREGOS_RUN_TOKEN", TOKEN)
    runner_settings.run_token = TOKEN
    return runner_settings


def _bridge(settings: RunnerSettings, path: str) -> str:
    """The bridge's address: the tool server's own host and port."""
    parts = urlsplit(settings.tools_url)
    return f"{parts.scheme}://{parts.netloc}/bridge/v1{path}"


def _calls(settings: RunnerSettings) -> list[dict[str, Any]]:
    return [
        {"url": _bridge(settings, "/scripts"), "body": {"sha256": DIGEST, "text": SCRIPT}},
        {
            "url": _bridge(settings, "/tools/crm__list_contacts"),
            "body": {"q": "acme"},
            "headers": {"X-Choregos-Script": DIGEST},
        },
        {"url": _bridge(settings, "/tools"), "method": "GET"},
        {"url": _bridge(settings, "/tools/crm__list_contacts"), "body": {}},
    ]


async def _run(stage_input: StageInput, settings: RunnerSettings) -> tuple[Any, FakeInternalClient, Any]:
    agent_script(
        {"turns": [{"http": _calls(settings), "result": valid_result()}]}, stage_input, settings.workspace
    )
    client = FakeInternalClient(stage_input)
    client.run_tools = [{"name": "crm__list_contacts", "x-choregos-access": "read", "outputSchema": {}}]
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    seen = json.loads((agent_home(stage_input) / "http.json").read_text(encoding="utf-8"))
    return outcome, client, seen


def _events(client: FakeInternalClient, kind: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == kind]


async def test_a_script_reaches_its_tools_through_the_runner_s_server(
    stage_input: StageInput, settings: RunnerSettings
) -> None:
    stage_input.tools.scripts = ToolScripts(max_concurrent_calls=2)
    _, client, seen = await _run(stage_input, settings)

    statuses = [answer["status"] for answer in seen["answers"]]
    assert statuses == [201, 200, 200, 200], seen
    assert seen["answers"][1]["body"].encode() == client.tool_answer.content, "the API's answer as it came"
    assert json.loads(seen["answers"][2]["body"])["tools"] == client.run_tools, "the list, whole"
    # Relayed with the runner's own client, the one that holds the run token.
    assert client.relayed == [
        {"name": "crm__list_contacts", "arguments": b'{"q": "acme"}', "script": DIGEST},
        {"name": "crm__list_contacts", "arguments": b"{}", "script": None},
    ]
    assert _events(client, "tools.local") == [{"started": True, "bridge": True}]
    assert _events(client, "tools.script") == [
        {"sha256": DIGEST, "bytes": len(SCRIPT.encode()), "text": SCRIPT}
    ]
    calls = _events(client, "tools.script.call")
    assert [(c["tool"], c["status_code"], c["script_sha256"]) for c in calls] == [
        ("crm__list_contacts", 200, DIGEST),
        ("crm__list_contacts", 200, None),
    ]
    assert all(c["response_bytes"] == len(client.tool_answer.content) for c in calls)
    assert all(isinstance(c["duration_ms"], int) and c["duration_ms"] >= 0 for c in calls)
    # The same lines in the run's transcript, which the journal writes as it records.
    transcript = (settings.workspace / ".choregos" / "transcript.jsonl").read_text(encoding="utf-8")
    assert '"tools.script.call"' in transcript and DIGEST in transcript

    # No credential: the agent's process holds no run token, and none came back to it or went to the journal.
    assert "CHOREGOS_RUN_TOKEN" not in seen["run_variables"], seen["run_variables"]
    assert TOKEN not in json.dumps(seen)
    assert TOKEN not in json.dumps(client.events) and TOKEN not in transcript


async def test_without_scripts_the_run_is_served_no_bridge(
    stage_input: StageInput, settings: RunnerSettings
) -> None:
    assert stage_input.tools.scripts is None
    _, client, seen = await _run(stage_input, settings)

    assert [answer["status"] for answer in seen["answers"]] == [404, 404, 404, 404], seen
    assert client.relayed == []
    assert _events(client, "tools.local") == [{"started": True}], "the event as before ADR 0122"
    assert _events(client, "tools.script") == [] and _events(client, "tools.script.call") == []


async def test_the_bridge_opens_no_port_the_agent_did_not_already_have(
    stage_input: StageInput, settings: RunnerSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With a proxy, the confinement holds the agent to a list of ports (ADR 0094).
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    _, without, _ = await _run(stage_input, settings)

    offered = stage_input.model_copy(deep=True)
    offered.tools.scripts = ToolScripts()
    assert offered.repo is not None
    offered.repo.work_branch = "choregos/1-total-scripts"
    again = settings.model_copy(update={"workspace": tmp_path / "workspace-scripts"})
    _, with_scripts, seen = await _run(offered, again)

    [before] = _events(without, "agent.confinement")
    [after] = _events(with_scripts, "agent.confinement")
    assert before["connect_ports"] is not None and port_of(settings.tools_url) in before["connect_ports"]
    assert after["connect_ports"] == before["connect_ports"], "the bridge is on the tool server's port"
    assert [answer["status"] for answer in seen["answers"]] == [201, 200, 200, 200], seen


class _HeldPost:
    """The internal client's `post_events` as it really behaves: one batch at a time, each written
    out (serialized) when its turn comes, then slow on the network."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.release = asyncio.Event()
        self.sent: list[int] = []

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        async with self.lock:
            written = [event["seq"] for event in events]
            await self.release.wait()
            self.sent.extend(written)
        return len(events)


async def test_an_event_recorded_while_a_batch_is_on_its_way_is_not_lost(tmp_path: Path) -> None:
    """The bridge records from its own requests, while the runner's journal sends a batch."""
    client = _HeldPost()
    journal = EventJournal(client, tmp_path / "transcript.jsonl", batch_size=1)  # type: ignore[arg-type]
    first = asyncio.create_task(journal.record("session/update", {"n": 1}))
    await asyncio.sleep(0)
    second = asyncio.create_task(journal.record("tools.script.call", {"n": 2}))
    await asyncio.sleep(0)
    client.release.set()
    await asyncio.gather(first, second)
    await journal.flush()

    assert sorted(set(client.sent)) == [1, 2], client.sent
