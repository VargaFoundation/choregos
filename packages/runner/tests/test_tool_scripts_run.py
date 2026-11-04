"""A run offered tool scripts gives its agent the client `choregos_tools` and the instructions; a run
that is not gives it nothing (ADR 0122 §3, §5, G9).

The fake agent writes a script under `$TMPDIR` and runs it with its own environment, as an agent's
shell does; the script calls its run's tools through the runner's real tool server and bridge, which
relay to the fake internal API.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput, ToolScripts
from choregos_runner import tool_scripts
from choregos_runner.backends import known_backends
from choregos_runner.backends.code import CodeBackend
from choregos_runner.config import RunnerSettings
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from choregos_runner.workspace import agent_home
from choregos_tools_mcp.client import Relayed

from .conftest import FakeInternalClient, agent_script, valid_result

TOKEN = "rt-runner-5e1d2c3b4a59687766554433"
HEADING = "## Calling tools from a script"
#: A rule set whose raw description the model never needs to read: only the script does.
RULES = [
    {"id": 17, "interface": "wan", "source": "PARTNERS", "descr": "RAW-RULE-NOT-FOR-THE-MODEL"},
    {"id": 3, "interface": "wan", "source": "any", "descr": "decoy"},
]
ALIASES = [{"name": "PARTNERS", "content": ["0.0.0.0/0"]}]
INTERFACES = [{"name": "wan", "addr": "203.0.113.1"}]
#: The run's tools as `getRunTools` lists them.
RUN_TOOLS = [
    {
        "name": f"pfsense__{operation}",
        "description": f"[pfsense] {operation.replace('_', ' ').capitalize()}.",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
        "x-choregos-access": "read",
    }
    for operation in ("list_interfaces", "list_firewall_rules", "list_aliases")
]
#: Three dependent calls, joined; the script prints one line: its digest of what it found.
SCRIPT = """\
import hashlib
from choregos_tools import pfsense

interface = pfsense.list_interfaces()["interfaces"][0]["name"]
rules = pfsense.list_firewall_rules(interface=interface)["rules"]
aliases = pfsense.list_aliases(name=rules[0]["source"])["aliases"]
names = {alias["name"] for alias in aliases}
exposed = sorted(f"{interface}#{rule['id']}" for rule in rules if rule["source"] in names)
print(hashlib.sha256(",".join(exposed).encode()).hexdigest()[:16])
"""
EXPECTED = hashlib.sha256(b"wan#17").hexdigest()[:16]


def _served(structured: dict[str, Any]) -> Relayed:
    """`callRunTool`'s answer for a directory connector's read (`broker._call`)."""
    result = {"content": [{"type": "text", "text": json.dumps(structured)}], "structuredContent": structured}
    body = {"status_code": 200, "result": result, "remaining": 7}
    return Relayed(200, json.dumps(body).encode(), "application/json")


@pytest.fixture(autouse=True)
def own_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The runner's temporary directory, the agent's `$HOME` and the client's, per test: `$TMPDIR`
    too, as in a pod, where the runner's temporary directory is the one its environment names."""
    own = tmp_path / "tmp"
    own.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(own))
    monkeypatch.setenv("TMPDIR", str(own))
    return own


@pytest.fixture
def settings(runner_settings: RunnerSettings, monkeypatch: pytest.MonkeyPatch) -> RunnerSettings:
    monkeypatch.setenv("CHOREGOS_RUN_TOKEN", TOKEN)
    runner_settings.run_token = TOKEN
    return runner_settings


async def _run(
    stage_input: StageInput, settings: RunnerSettings, script: str
) -> tuple[FakeInternalClient, Any]:
    # `-S`: the script imports the standard library and `PYTHONPATH`, nothing of the runner's venv.
    scripts = [{"name": "audit.py", "text": script, "python": [sys.executable, "-S"]}]
    stage_input.agent.launch.env["PYTHONPATH"] = "/opt/agent-own"
    agent_script({"turns": [{"scripts": scripts, "result": valid_result()}]}, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    client.run_tools = RUN_TOOLS
    client.tool_answers = {
        "pfsense__list_interfaces": _served({"interfaces": INTERFACES}),
        "pfsense__list_firewall_rules": _served({"rules": RULES, "count": len(RULES)}),
        "pfsense__list_aliases": _served({"aliases": ALIASES, "count": 1}),
    }
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    ran = json.loads((agent_home(stage_input) / "scripts.json").read_text(encoding="utf-8"))
    return client, ran


def _events(client: FakeInternalClient, kind: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in client.events if event["type"] == kind]


async def test_a_script_chains_three_calls_and_the_model_reads_one_line(
    stage_input: StageInput, settings: RunnerSettings
) -> None:
    stage_input.tools.scripts = ToolScripts(max_concurrent_calls=2)
    client, ran = await _run(stage_input, settings, SCRIPT)

    [run] = ran["runs"]
    assert run == {"code": 0, "stdout": f"{EXPECTED}\n", "stderr": ""}, run
    # Chained: each call's arguments come from the answer before it, through the real bridge.
    digest = hashlib.sha256(SCRIPT.encode()).hexdigest()
    assert [(c["name"], json.loads(c["arguments"]), c["script"]) for c in client.relayed] == [
        ("pfsense__list_interfaces", {}, digest),
        ("pfsense__list_firewall_rules", {"interface": "wan"}, digest),
        ("pfsense__list_aliases", {"name": "PARTNERS"}, digest),
    ]
    assert _events(client, "tools.script") == [
        {"sha256": digest, "bytes": len(SCRIPT.encode()), "text": SCRIPT}
    ]
    calls = _events(client, "tools.script.call")
    assert [(c["tool"], c["status_code"], c["script_sha256"]) for c in calls] == [
        ("pfsense__list_interfaces", 200, digest),
        ("pfsense__list_firewall_rules", 200, digest),
        ("pfsense__list_aliases", 200, digest),
    ]
    assert _events(client, "tools.client") == [{"written": True, "tools": 3, "modules": ["pfsense"]}]
    # What the tools returned stayed in the script: not in the journal, not in what the agent printed.
    transcript = (settings.workspace / ".choregos" / "transcript.jsonl").read_text(encoding="utf-8")
    assert "RAW-RULE-NOT-FOR-THE-MODEL" not in transcript + json.dumps(client.events) + run["stdout"]


#: Every backend whose agent has a shell — the shipped ACP agents and an agent written in code: the
#: runner sets the environment, whichever starts it.
SHELL_BACKENDS = (*known_backends(), CodeBackend.name)


@pytest.mark.parametrize("backend", SHELL_BACKENDS)
async def test_the_agent_gets_the_client_its_environment_and_the_instructions(
    stage_input: StageInput,
    settings: RunnerSettings,
    own_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    # A pod whose environment names no `TMPDIR`: the instructions' `$TMPDIR` must still be a directory
    # the agent may write, the runner's own.
    monkeypatch.delenv("TMPDIR")
    stage_input.agent.backend = backend
    stage_input.model.provider_model = "anthropic/claude-sonnet-5"  # what claude-code requires
    stage_input.tools.scripts = ToolScripts()
    _, ran = await _run(stage_input, settings, "print('hello')\n")

    directory = own_tmp / "choregos" / "tools"
    assert ran["env"] == {
        "CHOREGOS_TOOLS_DIR": str(directory),
        "CHOREGOS_TOOLS_BRIDGE": tool_scripts.bridge_url(settings.tools_url),
        "PYTHONPATH": os.pathsep.join([str(directory), "/opt/agent-own"]),
        "TMPDIR": str(own_tmp),
    }
    assert (directory / "choregos_tools" / "pfsense.py").is_file()
    assert "- pfsense.list_aliases — read — List aliases." in (directory / "INDEX.md").read_text()
    task = (settings.workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
    assert task.endswith(tool_scripts.INSTRUCTIONS) and task.count(HEADING) == 1
    assert len(tool_scripts.INSTRUCTIONS.strip().splitlines()) <= 25
    # Outside the workspace: nothing of the client is in the tree, nor in what the run committed.
    workspace = settings.workspace
    assert not [p for p in workspace.rglob("*") if "choregos_tools" in p.parts or p.name == "INDEX.md"]


async def test_without_scripts_the_agent_gets_nothing_new(
    stage_input: StageInput, settings: RunnerSettings, own_tmp: Path
) -> None:
    assert stage_input.tools.scripts is None
    client, ran = await _run(stage_input, settings, "print('hello')\n")

    assert ran["env"] == {
        "CHOREGOS_TOOLS_DIR": None,
        "CHOREGOS_TOOLS_BRIDGE": None,
        "PYTHONPATH": "/opt/agent-own",
        "TMPDIR": str(own_tmp),
    }
    assert not (own_tmp / "choregos").exists(), "no client written"
    task = (settings.workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
    assert HEADING not in task and "choregos_tools" not in task
    assert _events(client, "tools.client") == []


async def test_a_client_that_cannot_be_written_leaves_the_run_without_it(
    stage_input: StageInput, settings: RunnerSettings, own_tmp: Path
) -> None:
    (own_tmp / "choregos").write_text("a file where the client's directory goes", encoding="utf-8")
    stage_input.tools.scripts = ToolScripts()
    client, ran = await _run(stage_input, settings, "print('hello')\n")

    [event] = _events(client, "tools.client")
    assert event["written"] is False and event["error"]
    assert ran["env"]["CHOREGOS_TOOLS_DIR"] is None
    assert HEADING not in (settings.workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
