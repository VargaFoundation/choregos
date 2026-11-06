"""A plugin's agent cases in the scripted matrix, against its server's fake (#1372, E3 and C1).

The matrix names a plugin's case `plugin:<plugin>/<case>` and loads it from the plugin (here the
offline plugins are `tests/fixtures/plugins`, whose `servicenow` mirrors choregos-agents#8). Its
connector `plugin:servicenow/servicenow` is the plugin's server, reached over MCP as the broker reaches
it, on a fake registered for it: here `FakePluginServer` on the recording of that server's own fake
(`tests/fixtures/plugin_fakes/servicenow.json`). The server's declaration governs its tools: what it
does not declare is not offered, a read is allowed, a write runs as a governed action when the case
allows writes, and `human_approval` (the comment to the caller, the resolution) is only ever proposed.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.fakes.plugin_servers import FakePluginServer, use_fake_plugin_server
from choregos_core.agent_catalogue import OFFLINE_ENV
from choregos_orchestrator.activities import eval_doors, evals
from choregos_playbooks.evals.agents import judge

from .conftest import Fixture

ROOT = Path(__file__).resolve().parents[3]
E3, C1 = "plugin:servicenow/itsm-triage-similar-incidents", "plugin:servicenow/itsm-triage-vague-incident"
INCIDENT, VAGUE = "4cf228998fe835fd4694475612ed8445", "d3010b741883783c781f7241153776f7"


@pytest.fixture
def servicenow(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakePluginServer]]:
    """The fixture plugins offline, and the servicenow server's recorded fake registered for it."""
    monkeypatch.setenv(OFFLINE_ENV, str(ROOT / "tests" / "fixtures" / "plugins"))
    built: list[FakePluginServer] = []

    def factory(scene: str) -> Any:
        built.append(
            FakePluginServer.from_recording(ROOT / "tests" / "fixtures" / "plugin_fakes" / "servicenow.json")
        )
        return built[-1].transport()

    use_fake_plugin_server("servicenow", "servicenow", factory)
    yield built
    use_fake_plugin_server("servicenow", "servicenow", None)


def _case(name: str) -> dict[str, Any]:
    case = evals.load_agent_case(name)
    assert case is not None, name
    return case


def _cell(tools: str, **extra: Any) -> dict[str, Any]:
    return {
        "project_slug": "billing-api",
        "backend": "claude-code",
        "model": "platform/standard",
        "with_memory": False,
        "context": "none",
        "tools": tools,
        "fixtures": [],
        **extra,
    }


async def _played(case: dict[str, Any], cell: str) -> tuple[eval_doors.Played, dict[str, Any]]:
    played = await eval_doors.play(case, cell)
    verdict = judge(
        case, played.answer, {**played.observation, "requests": case["scripted_usage"][cell]["requests"]}
    )
    return played, verdict


# ───────────────────────── a plugin's case, by name ─────────────────────────


def test_a_plugin_case_name_resolves_to_the_case_its_plugin_ships(servicenow: list[FakePluginServer]) -> None:
    case = _case(E3)
    assert (case["name"], case["plugin"], case["agent"]) == (
        "itsm-triage-similar-incidents",
        "servicenow",
        "itsm-triager",
    )
    assert case["connectors"] == {"servicenow": {"kind": "plugin:servicenow/servicenow", "scene": "default"}}
    assert evals.load_agent_case("plugin:servicenow/no-such-case") is None
    assert evals.load_agent_case("plugin:no-such-plugin/itsm-triage-similar-incidents") is None
    from choregos_playbooks.evals.agents import case_instructions, score_instructions

    instructions = case_instructions(case)
    assert instructions is not None and score_instructions(case, instructions) == [], "its plugin's agent"


# ───────────────────────── governed by the server's declaration ─────────────────────────


async def test_the_server_s_declaration_governs_its_tools(servicenow: list[FakePluginServer]) -> None:
    api = await eval_doors.EvalApi(_case(E3)).ready()
    listed = {tool["name"]: tool for tool in api.listed()}
    assert listed["servicenow__get_record"]["x-choregos-access"] == "read"
    # The case allows writes: the work note runs as a governed action; the comment to the caller and the
    # resolution carry `human_approval`, which no policy lowers.
    assert listed["servicenow__add_work_note"]["x-choregos-access"] == "write"
    assert listed["servicenow__add_comment"]["x-choregos-access"] == "approval"
    assert listed["servicenow__resolve_incident"]["x-choregos-access"] == "approval"
    assert "needs a human approval" in listed["servicenow__add_comment"]["description"]
    assert {"sys_id", "limit"} <= set(listed["servicenow__similar_incidents"]["inputSchema"]["properties"])
    [fake] = servicenow

    proposed = (
        await api.call("servicenow__add_comment", {"table": "incident", "sys_id": INCIDENT, "text": "Hi"})
    ).json()
    assert (proposed["status_code"], proposed["result"]["status"]) == (202, "pending_approval")
    assert proposed["result"]["decision_path"].startswith("/p/evals/actions/")
    assert fake.called("add_comment") == [], "proposed: nothing reached the server"

    note = {"table": "incident", "sys_id": INCIDENT, "text": "x", "action_key": "the agent's own"}
    written = (await api.call("servicenow__add_work_note", note)).json()
    [sent] = fake.called("add_work_note")
    assert sent["action_key"] != "the agent's own" and len(sent["action_key"]) == 24, (
        "the platform's, as a governed action fills it"
    )
    assert written["status_code"] == 422, (
        "the server's error result, as the broker relays it (not in the recording)"
    )

    refused = await api.call("servicenow__similar_incidents", {"sys_id": INCIDENT, "limit": "ten"})
    assert (
        refused.status_code == 400 and "schema of servicenow__similar_incidents" in refused.json()["detail"]
    )
    assert (await api.call("servicenow__drop_table", {})).status_code == 404, "not declared, not offered"


async def test_under_a_policy_where_every_write_waits_the_work_note_is_proposed(
    servicenow: list[FakePluginServer],
) -> None:
    api = await eval_doors.EvalApi({**_case(E3), "policy": {"writes": "approval"}}).ready()
    answer = (
        await api.call("servicenow__add_work_note", {"table": "incident", "sys_id": INCIDENT, "text": "x"})
    ).json()
    assert answer["status_code"] == 202 and servicenow[0].called("add_work_note") == []


# ───────────────────────── E3 and C1, played in both cells and judged ─────────────────────────


@pytest.mark.parametrize(("name", "cell"), [(E3, "mcp"), (E3, "scripts"), (C1, "mcp"), (C1, "scripts")])
async def test_a_plugin_case_passes_the_judge_in_each_of_its_cells(
    servicenow: list[FakePluginServer], name: str, cell: str
) -> None:
    played, verdict = await _played(_case(name), cell)
    if played.script is not None:
        assert played.script["exit_code"] == 0, played.script["stderr"]
    assert [c for c in played.observation["calls"] if c["status_code"] not in (200, 202)] == []
    assert verdict["ok"], verdict["reason"]


async def test_e3_s_script_fans_out_through_the_bridge_and_only_the_work_note_reaches_the_server(
    servicenow: list[FakePluginServer],
) -> None:
    played, _ = await _played(_case(E3), "scripts")
    assert played.door == "bridge" and played.script is not None
    assert played.script["modules"] == ["servicenow"]
    assert [c["origin"] for c in played.observation["calls"]] == ["script"] * 25
    stdout = played.script["stdout"]
    assert "CHG0003311 resolved 2 of them: INC0012102, INC0012160\n" in stdout
    assert "KB0010042: VPN to the Lyon site drops: check the MTU on the Lyon concentrator\n" in stdout
    assert "- servicenow__add_comment: PROPOSED, NOT DONE: servicenow__add_comment" in stdout
    assert [(a["tool"], a["status"]) for a in played.observation["actions"]] == [
        ("servicenow__add_work_note", "succeeded"),
        ("servicenow__add_comment", "pending_approval"),
    ]
    [fake] = servicenow
    [note] = fake.called("add_work_note")
    assert "CHG0003311 resolved INC0012102, INC0012160" in note["text"] and len(note["action_key"]) == 24
    assert fake.called("add_comment") == [] and fake.called("resolve_incident") == []


async def test_e3_fails_when_the_work_note_waits_for_a_person(servicenow: list[FakePluginServer]) -> None:
    """`executed_actions` bites: under a policy where every write waits, the note is only proposed."""
    case = {**copy.deepcopy(_case(E3)), "policy": {"writes": "approval"}}
    _, verdict = await _played(case, "scripts")
    assert not verdict["ok"] and verdict["assertions_failed"] == ["executed_actions: 0, expected 1"]
    assert servicenow[0].called("add_work_note") == []


async def test_c1_fails_when_the_vague_incident_is_resolved(servicenow: list[FakePluginServer]) -> None:
    """`proposed_actions` and `no_tool_called` bite; the resolution, a person's, is never played."""
    case = copy.deepcopy(_case(C1))
    case["evidence"].append(
        {
            "tool": "servicenow__resolve_incident",
            "arguments": {
                "sys_id": VAGUE,
                "close_code": "Solution provided",
                "close_notes": "See KB0010003.",
            },
        }
    )
    _, verdict = await _played(case, "mcp")
    assert not verdict["ok"]
    assert verdict["assertions_failed"] == [
        "proposed_actions: 1, expected 0",
        "no_tool_called: called servicenow__resolve_incident",
    ]
    assert servicenow[0].called("resolve_incident") == []


# ───────────────────────── the scripted matrix ─────────────────────────


async def test_the_scripted_matrix_plays_e3_and_c1_in_both_cells(
    setup: Fixture, servicenow: list[FakePluginServer]
) -> None:
    cells = {
        tools: await evals.run_eval_cell(_cell(tools, agent_cases=[E3, C1])) for tools in ("mcp", "scripts")
    }
    for tools, cell in cells.items():
        assert [(d["fixture"], d["agent"], d.get("ok")) for d in cell["details"]] == [
            (E3, "itsm-triager", True),
            (C1, "itsm-triager", True),
        ], (tools, [d["reason"] for d in cell["details"]])
        assert (cell["cases"], cell["skipped"], cell["success_rate"]) == (2, 0, 1.0)
    # The cell's door, what both runs were offered (S57-11b); C1 has no script, so it used none.
    assert [(d["door"], d["scripts_used"]) for d in cells["scripts"]["details"]] == [
        ("scripts", 1),
        ("scripts", 0),
    ]
    assert cells["scripts"]["calls_by_origin"] == {"script": 25, "model": 5}
    assert (cells["mcp"]["proposed_actions"], cells["mcp"]["executed_actions"]) == (2, 1)


async def test_without_a_fake_of_the_server_the_case_is_skipped_not_failed(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OFFLINE_ENV, str(ROOT / "tests" / "fixtures" / "plugins"))
    cell = await evals.run_eval_cell(_cell("mcp", agent_cases=[E3]))
    [detail] = cell["details"]
    assert detail["skipped"] and "no fake of the plugin server servicenow/servicenow" in detail["reason"]
    assert cell["cases"] == 0


async def test_a_scene_the_fakes_do_not_have_is_still_the_case_s_error(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a plugin's server without a fake is skipped: a case naming a scene that does not exist fails."""
    from choregos_adapters.errors import AdapterError

    broken = {
        **_case("netops-ssh-exposure"),
        "connectors": {"pfsense": {"kind": "firewall", "scene": "nope"}},
    }
    monkeypatch.setattr(evals, "load_agent_case", lambda name: copy.deepcopy(broken))
    with pytest.raises(AdapterError, match="has no scene 'nope'"):
        await evals.run_eval_cell(_cell("mcp", agent_cases=["netops-ssh-exposure"]))
