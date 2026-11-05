"""The matrix compares the two doors (S57-09, ADR 0122 §9).

In a scripted cell, an agent case goes through the platform's real doors on the fakes: the `mcp` cell's
calls through the run's MCP server, the `scripts` cell's through a script run with `python3 -S`, the
generated client `choregos_tools` and the real tool bridge; the fake gateway bills the case's
`scripted_usage` for the cell. In a live cell, the cell forces `tools.scripts` on or off in the stage
input, and a run that did not get the cell's door is skipped.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes import entra as fake_entra
from choregos_contracts import Diagnostics, Evidence, StageInput, StageResult, StageStatus
from choregos_orchestrator.activities import eval_doors, evals
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.config import reset_settings_cache

from .conftest import Fixture

E1, E4 = "netops-ssh-exposure", "identity-offboarding"


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


def _by_case(cell: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for detail in cell["details"]:
        found.setdefault(detail["fixture"], []).append(detail)
    return found


# ───────────────────────── the scripts cell runs real scripts through the bridge ─────────────────────────


async def test_e1_s_script_chains_its_reads_through_the_bridge_and_the_mcp_cell_through_mcp() -> None:
    case = evals.load_agent_case(E1)
    assert case is not None
    scripted = await eval_doors.play(case, "scripts")
    assert scripted.door == "bridge" and scripted.script is not None
    assert scripted.script["exit_code"] == 0, scripted.script["stderr"]
    assert scripted.script["modules"] == ["pfsense"], "the generated client, one module per server"
    calls = scripted.observation["calls"]
    assert [c["origin"] for c in calls] == ["script"] * 8, "origin `script`, from the bridge's own header"
    assert {c["tool"] for c in calls} == {
        "pfsense__list_interfaces",
        "pfsense__list_aliases",
        "pfsense__list_firewall_rules",
    }
    [script] = scripted.observation["scripts"]
    assert script == {
        "sha256": scripted.script["sha256"],
        "calls": 8,
        "failed_calls": 0,
        "exit_code": 0,
        "errored": False,
    }, "the bridge journaled the script and each of its calls"
    # The model reads what the script printed, not the 184 rules.
    assert scripted.script["stdout"].count("\n") == 4 and "3 of 184 rules" in scripted.script["stdout"]
    assert "`wan#17`" in scripted.answer and "Temporary vendor SSH" in scripted.answer

    through_mcp = await eval_doors.play(case, "mcp")
    assert through_mcp.door == "mcp" and through_mcp.script is None
    assert [c["origin"] for c in through_mcp.observation["calls"]] == ["model"] * 8
    assert through_mcp.observation["scripts"] == []


async def test_e4_s_script_receives_eight_proposals_and_nothing_is_written() -> None:
    case = evals.load_agent_case(E4)
    assert case is not None
    played = await eval_doors.play(case, "scripts")
    assert played.api is not None and played.script is not None
    directory = played.api.scenes["entra"].fake
    [(account_id, account)] = directory.accounts.items()
    assert fake_entra.state(directory) == {
        "accounts": {account_id: {**account, "accountEnabled": True}},
        "groups": {group: [account_id] for group in fake_entra.OFFBOARDING_GROUPS},
        "revoked_sessions": [],
    }, "still enabled, in its six groups, its sessions alive"
    assert directory.received == [], "no write, no read: the API proposed and sent nothing"
    assert [(a["tool"], a["status"]) for a in played.observation["actions"]] == [
        ("entra__revoke_sessions", "pending_approval"),
        *[("entra__remove_from_group", "pending_approval")] * 6,
        ("entra__disable_user", "pending_approval"),
    ]
    # The client's `Proposal`, printed by the script: proposed, not done, with its decision path.
    lines = played.script["stdout"].splitlines()
    assert sum("PROPOSED, NOT DONE" in line and "/p/evals/actions/" in line for line in lines) == 8
    assert lines[-1] == "8 of 8 writes proposed, 0 done."


async def test_e4_fails_its_assertions_when_its_writes_are_not_under_approval() -> None:
    """The failing fixture of `executed_actions` and `proposed_actions`, end to end: the same script under
    a policy that allows the writes executes them, and the case says so."""
    from choregos_playbooks.evals.agents import judge

    case = evals.load_agent_case(E4)
    assert case is not None
    allowed = {**copy.deepcopy(case), "policy": {"writes": "allowed"}}
    played = await eval_doors.play(allowed, "scripts")
    assert played.api is not None
    directory = played.api.scenes["entra"].fake
    assert not any(account["accountEnabled"] for account in directory.accounts.values()), "it really ran"
    verdict = judge(allowed, played.answer, {**played.observation, "requests": 3})
    assert not verdict["ok"]
    assert "executed_actions: 8, expected 0" in verdict["assertions_failed"]
    assert "proposed_actions: 0, expected 8" in verdict["assertions_failed"]


async def test_a_script_that_raises_is_a_script_in_error() -> None:
    case = evals.load_agent_case(E1)
    assert case is not None
    broken = copy.deepcopy(case)
    broken["scripted_script"]["text"] = "from choregos_tools import call\ncall('pfsense__drop_everything')\n"
    played = await eval_doors.play(broken, "scripts")
    assert played.script is not None and played.script["exit_code"] == 1
    assert "ToolError" in played.script["stderr"]
    [script] = played.observation["scripts"]
    assert (script["failed_calls"], script["errored"]) == (1, True)
    assert played.observation["calls"] == [], "an unknown tool reaches nothing"


# ───────────────────────── a scripted cell ─────────────────────────


async def test_a_scripted_matrix_plays_the_agent_cases_in_both_cells_and_bills_each_door(
    setup: Fixture,
) -> None:
    cells = {
        tools: await evals.run_eval_cell(_cell(tools, agent_cases=[E1, E4], repetitions=2))
        for tools in ("mcp", "scripts")
    }
    mcp, scripts = cells["mcp"], cells["scripts"]
    assert (mcp["cases"], scripts["cases"]) == (4, 4) and mcp["success_rate"] == scripts[
        "success_rate"
    ] == 1.0
    assert {d["door"] for d in scripts["details"]} == {"bridge"} and {d["door"] for d in mcp["details"]} == {
        "mcp"
    }
    # The round trips the cases script, as the gateway counted them: 7 and 10 by MCP, 3 and 3 by script.
    assert (mcp["median_requests"], scripts["median_requests"]) == (8.5, 3.0)
    assert mcp["calls_by_origin"] == {"model": 32} and scripts["calls_by_origin"] == {"script": 32}
    assert (scripts["scripts"], scripts["script_errors"], scripts["script_error_rate"]) == (4, 0, 0.0)
    assert (mcp["scripts"], mcp["script_error_rate"], mcp["script_adoption"]) == (0, None, 0.0)
    assert scripts["script_adoption"] == 1.0 and scripts["median_platform_tool_calls"] == 8.0
    assert (mcp["proposed_actions"], mcp["executed_actions"]) == (16, 0), "E4's eight, twice"
    assert (scripts["proposed_actions"], scripts["executed_actions"]) == (16, 0)
    e1 = _by_case(scripts)[E1]
    assert [(d["repetition"], d["agent"], d["ok"]) for d in e1] == [(0, "netops", True), (1, "netops", True)]
    assert all(d["cost_usd"] > 0 for d in scripts["details"])
    assert all(
        m < s
        for m, s in zip(
            (d["cost_usd"] for d in scripts["details"]), (d["cost_usd"] for d in mcp["details"]), strict=True
        )
    )

    published = await evals.publish_matrix({"project_slug": setup.project_slug, "results": [mcp, scripts]})
    verdicts = {(c["group"], c["key"]): c["verdict"] for c in published["comparisons"]}
    assert verdicts == {
        ("agent", "netops"): "better",
        ("agent", "identity-operator"): "better",
        ("case", E1): "better",
        ("case", E4): "better",
    }, "cheaper and as good"


async def test_a_scripted_matrix_where_scripts_is_cheaper_and_worse_says_worse(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = evals.load_agent_case(E1)
    assert case is not None
    blind = copy.deepcopy(case)
    blind["scripted_script"]["text"] = "print('no rule exposes SSH')\n"  # cheap, and wrong
    monkeypatch.setattr(evals, "load_agent_case", lambda name: copy.deepcopy(blind if name == E1 else case))
    results = [
        await evals.run_eval_cell(_cell(t, agent_cases=[E1], repetitions=3)) for t in ("mcp", "scripts")
    ]
    assert [r["success_rate"] for r in results] == [1.0, 0.0]
    assert "missing from the answer" in results[1]["details"][0]["reason"]
    assert results[1]["scripts"] == 0, "a script that never called the bridge is not counted"
    published = await evals.publish_matrix({"project_slug": setup.project_slug, "results": results})
    [netops, *_] = published["comparisons"]
    assert (netops["key"], netops["verdict"]) == ("netops", "worse")
    assert netops["cost_relative_difference"]["high"] is None, "no success by script: unbounded"


async def test_the_coding_tickets_offered_the_door_tie_and_say_not_worse(setup: Fixture) -> None:
    """C2: the coding tickets, played in both cells; the scripted change is the same, the door unused."""
    results = [
        await evals.run_eval_cell(
            _cell(t, fixtures=["py-rounding-bug", "py-volume-discount"], with_memory=True)
        )
        for t in ("mcp", "scripts")
    ]
    assert [r["success_rate"] for r in results] == [1.0, 1.0]
    assert results[1]["calls_by_origin"] == {} and results[1]["script_adoption"] == 0.0
    published = await evals.publish_matrix({"project_slug": setup.project_slug, "results": results})
    by_key = {(c["group"], c["key"]): c for c in published["comparisons"]}
    coding = by_key[("agent", "role:implement")]
    assert coding["verdict"] == "not_worse" and coding["pairs"] == 2
    assert coding["cost_relative_difference"] == {"estimate": 0.0, "low": 0.0, "high": 0.0}


async def test_only_an_mcp_cell_validates_a_backend(setup: Fixture) -> None:
    from choregos_api.db.models import ModelProfileRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    scripts = await evals.run_eval_cell(_cell("scripts", agent_cases=[E1]))
    assert scripts["validated"]
    await evals.publish_matrix({"project_slug": setup.project_slug, "results": [scripts]})
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ModelProfileRow).where(ModelProfileRow.litellm_model == "platform/standard")
            )
        ).scalar_one()
        assert row.validated_backends == [], "a backend that only passed with scripts is not validated"
        assert row.params["matrix"][0]["tools"] == "scripts"
        assert row.params["matrix_comparisons"] == []
    mcp = await evals.run_eval_cell(_cell("mcp", agent_cases=[E1]))
    await evals.publish_matrix({"project_slug": setup.project_slug, "results": [mcp, scripts]})
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ModelProfileRow).where(ModelProfileRow.litellm_model == "platform/standard")
            )
        ).scalar_one()
        assert row.validated_backends == ["claude-code"]
        assert [c["verdict"] for c in row.params["matrix_comparisons"]] == ["better", "better"]


async def test_a_case_not_played_in_a_cell_and_an_unknown_door_are_refused(setup: Fixture) -> None:
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError, match="unknown tools door"):
        await evals.run_eval_cell(_cell("both", agent_cases=[E1]))
    with pytest.raises(ApplicationError, match="repetitions"):
        await evals.run_eval_cell(_cell("mcp", agent_cases=[E1], repetitions=0))
    cell = await evals.run_eval_cell(_cell("mcp", agent_cases=["no-such-case"]))
    assert cell["skipped"] == 1 and "no agent case" in cell["details"][0]["reason"]


def test_the_scripted_usage_of_a_cell() -> None:
    usages = {
        "scripted_usage": {"none": {"t": 1}, "rtk": {"t": 2}, "none/scripts": {"t": 3}, "scripts": {"t": 4}}
    }
    assert evals.scripted_usage(usages, "none", "mcp") == {"t": 1}
    assert evals.scripted_usage(usages, "rtk", "mcp") == {"t": 2}
    assert evals.scripted_usage(usages, "none", "scripts") == {"t": 3}, "`<context>/<tools>` first"
    assert evals.scripted_usage({"scripted_usage": {"scripts": {"t": 4}}}, "none", "scripts") == {"t": 4}
    assert evals.scripted_usage(usages, "rtk", "scripts") is None, "never another cell's usage"


async def test_a_usage_with_requests_is_billed_as_that_many_model_calls(setup: Fixture) -> None:
    gateway = setup.adapters.gateway
    key = await gateway.mint_key({"purpose": "eval"}, 50.0, 3600, ["platform/standard"])
    usage = {"requests": 7, "tokens_in": 214_001, "tokens_cached": 158_000, "tokens_out": 3_900, "turns": 7}
    spec = {"scripted_usage": {"mcp": usage}}
    assert (
        evals._bill_scripted(gateway, key.key_id, spec, "none", "platform/standard", {"source": "scripted"})
        == 7
    )
    spend = await gateway.spend(key.key_id)
    assert (spend.requests, spend.tokens_in, spend.tokens_cached, spend.tokens_out) == (
        7,
        214_001,
        158_000,
        3_900,
    )


# ───────────────────────── a live cell forces the door ─────────────────────────


@pytest.fixture
def scripts_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_TOOL_SCRIPTS", "on")
    reset_settings_cache()
    yield
    monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS", raising=False)
    reset_settings_cache()


@pytest.fixture
async def eval_project(setup: Fixture) -> Fixture:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model["memory"] = {**model.get("memory", {}), "enabled": False}
        model.pop("tools", None)  # the policy offers no script: the cell decides
        row.json_doc = model
    return setup


def _live(tools: str, **extra: Any) -> dict[str, Any]:
    return {**_cell(tools), "matrix_id": "evals-nightly", "fixture": "py-rounding-bug", **extra}


async def test_a_scripts_cell_forces_the_bridge_on_and_an_mcp_cell_off(
    eval_project: Fixture, scripts_on: None
) -> None:
    scripts = await evals.seed_eval_ticket(_live("scripts"))
    mcp = await evals.seed_eval_ticket(_live("mcp"))
    assert scripts["plan"]["eval_cell"]["tools"] == {"max_concurrent_calls": 4}
    assert mcp["plan"]["eval_cell"]["tools"] is None
    assert scripts["plan"]["work_item_id"] != mcp["plan"]["work_item_id"], "a ticket per door"
    offered = await stage_activities.prepare_stage(scripts["plan"])
    assert offered["stage_input"]["tools"]["scripts"] == {"max_concurrent_calls": 4}, (
        "though the policy is off"
    )
    plain = await stage_activities.prepare_stage(mcp["plan"])
    assert "scripts" not in plain["stage_input"]["tools"]
    again = await evals.seed_eval_ticket(_live("scripts", repetition=1))
    assert again["plan"]["work_item_id"] != scripts["plan"]["work_item_id"], "a ticket per repetition"


async def test_the_kill_switch_skips_the_scripts_cell_and_still_wins_at_prepare_time(
    eval_project: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = await evals.seed_eval_ticket(_live("scripts"))
    assert refused["skipped"] and "CHOREGOS_TOOL_SCRIPTS" in refused["reason"]
    assert not (await evals.seed_eval_ticket(_live("mcp")))["skipped"]
    monkeypatch.setenv("CHOREGOS_TOOL_SCRIPTS", "on")
    reset_settings_cache()
    seeded = await evals.seed_eval_ticket(_live("scripts"))
    monkeypatch.setenv("CHOREGOS_TOOL_SCRIPTS", "off")
    reset_settings_cache()
    try:
        prepared = await stage_activities.prepare_stage(seeded["plan"])
    finally:
        monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS")
        reset_settings_cache()
    assert "scripts" not in prepared["stage_input"]["tools"]


async def test_a_live_cell_skips_an_agent_case_where_the_deployment_serves_no_eval_fakes(
    eval_project: Fixture,
) -> None:
    """Since S57-11a a live cell plays agent cases, on fake scenes only (`test_eval_live_agent_cases.py`)."""
    seeded = await evals.seed_eval_ticket(_live("mcp", agent_case=E1, fixture=E1))
    assert seeded["skipped"] and "CHOREGOS_EVAL_FAKE_SCENES" in seeded["reason"]


async def _run(setup: Fixture, tools: str, events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """A live case's run with `events` in its journal, then judged in the cell `tools`."""
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    def agent(stage_input: StageInput) -> StageResult:
        setup.adapters.gateway.record_usage(
            stage_input.gateway_key.removeprefix("sk-fake-"),
            tokens_in=1000,
            tokens_out=100,
            model="platform/standard",
        )
        return StageResult(
            status=StageStatus.DONE,
            summary="eval",
            evidence=Evidence(facts=dict.fromkeys(stage_input.project.fact_commands, True)),
            diagnostics=Diagnostics(turns=3),
        )

    setup.adapters.executor.handler = agent
    seeded = await evals.seed_eval_ticket(_live(tools))
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    ids = {"project_id": seeded["plan"]["project_id"], "run_id": prepared["run_id"]}
    await stage_activities.start_run(
        {**ids, "work_item_id": seeded["plan"]["work_item_id"], "stage_input": prepared["stage_input"]}
    )
    awaited = await stage_activities.await_run({**ids, "timeout_minutes": 1, "poll_seconds": 0.05})
    await stage_activities.record_run_outcome({**ids, "result": awaited["result"]})
    await stage_activities.collect_spend(ids)
    async with session_scope() as session:
        for seq, (type_, payload) in enumerate(events, start=900):
            session.add(
                RunEvent(run_id=prepared["run_id"], seq=seq, type=type_, payload=payload, ts=utcnow())
            )
    verdict: dict[str, Any] = await evals.judge_eval_run(
        {**ids, "fixture": "py-rounding-bug", "context": "none", "tools": tools, "failure": ""}
    )
    return verdict


DIGEST = "a" * 64
CLIENT = ("tools.client", {"written": True, "tools": 3, "modules": ["pfsense"]})


async def test_a_scripts_run_whose_agent_got_its_client_is_judged(
    eval_project: Fixture, scripts_on: None
) -> None:
    verdict = await _run(eval_project, "scripts", [CLIENT])
    assert not verdict["skipped"] and verdict["ok"], verdict["reason"]
    assert verdict["observation"]["scripts"] == [], "offered and not used: faithful, adoption 0"


@pytest.mark.parametrize(
    ("tools", "events", "why"),
    [
        ("scripts", [], "no tools.client event"),
        ("scripts", [("tools.client", {"written": False, "error": "read-only"})], "read-only"),
        ("mcp", [CLIENT], "journaled tool scripts in an mcp cell"),
        ("mcp", [("tools.script", {"sha256": DIGEST, "bytes": 3})], "journaled tool scripts in an mcp cell"),
    ],
)
async def test_a_run_that_did_not_get_the_cell_s_door_is_skipped(
    eval_project: Fixture, scripts_on: None, tools: str, events: list[tuple[str, dict[str, Any]]], why: str
) -> None:
    verdict = await _run(eval_project, tools, events)
    assert verdict["skipped"] and why in verdict["reason"]


async def test_a_run_offered_scripts_in_an_mcp_cell_is_skipped(
    eval_project: Fixture, scripts_on: None
) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    verdict = await _run(eval_project, "mcp", [])
    assert not verdict["skipped"], verdict["reason"]
    async with session_scope() as session:
        run = (await session.execute(select(Run))).scalars().all()[-1]
        run.stage_input = {**run.stage_input, "tools": {"mcp": {}, "scripts": {"max_concurrent_calls": 4}}}
        run_id = run.id
    again = await evals.judge_eval_run(
        {"run_id": run_id, "fixture": "py-rounding-bug", "context": "none", "tools": "mcp", "failure": ""}
    )
    assert again["skipped"] and "offered tool scripts, the cell is mcp" in again["reason"]


async def test_a_live_run_s_tool_use_is_read_from_its_events_actions_and_journal(
    eval_project: Fixture, scripts_on: None
) -> None:
    from choregos_api.db.models import Action, Event, Organization, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    verdict = await _run(eval_project, "scripts", [CLIENT])
    async with session_scope() as session:
        run = (await session.execute(select(Run))).scalars().all()[-1]
        org = (await session.execute(select(Organization))).scalar_one()
        called = [
            ("pfsense__list_aliases", "script", 200),
            ("pfsense__list_aliases", "script", 500),
            ("x", None, 200),
        ]
        for tool, origin, code in called:
            payload = {"tool": tool, "status_code": code, **({"origin": origin} if origin else {})}
            session.add(
                Event(
                    project_id=eval_project.project_id,
                    type="choregos.tool.called",
                    subject=run.id,
                    payload=payload,
                    ts=utcnow(),
                )
            )
        session.add(
            Event(
                project_id=eval_project.project_id,
                type="choregos.security.injection_suspected",
                subject=run.id,
                payload={"tool": "web_fetch"},
                ts=utcnow(),
            )
        )
        for status in ("pending_approval", "succeeded"):
            session.add(
                Action(
                    org_id=org.id, project_id=eval_project.project_id, run_id=run.id, origin="tool",
                    kind="pfsense.delete_firewall_rule", title="t", status=status,
                )
            )  # fmt: skip
        run_id = run.id
    verdict = await evals.judge_eval_run(
        {"run_id": run_id, "fixture": "py-rounding-bug", "context": "none", "tools": "scripts", "failure": ""}
    )
    observation = verdict["observation"]
    assert [(c["tool"], c["origin"]) for c in observation["calls"]] == [
        ("pfsense__list_aliases", "script"),
        ("pfsense__list_aliases", "script"),
        ("x", "model"),  # an event from before S57-05 has no origin: the MCP door's
    ]
    assert observation["security_events"] == [
        {"type": "choregos.security.injection_suspected", "tool": "web_fetch"}
    ]
    assert observation["actions"] == [
        {"tool": "pfsense__delete_firewall_rule", "status": "pending_approval"},
        {"tool": "pfsense__delete_firewall_rule", "status": "succeeded"},
    ]
    assert observation["requests"] == 1
    line = evals._cell(_cell("scripts"), "none", [{**verdict, "fixture": "py-rounding-bug"}])
    assert line["calls_by_origin"] == {"model": 1, "script": 2}
    assert (line["proposed_actions"], line["executed_actions"]) == (1, 1)


@pytest.mark.parametrize(
    ("name", "code"), [("research-injected-page", 200), ("research-injected-page-block", 451)]
)
async def test_e5_s_injected_page_is_flagged_or_withheld_whatever_the_cell(name: str, code: int) -> None:
    """The guard runs on every result whoever calls: `warn` keeps it with its suspicions, `block` withholds
    it (451). E5 has no script: its `scripts` cell goes through the MCP door too, and says so."""
    from choregos_playbooks.evals.agents import judge

    case = evals.load_agent_case(name)
    assert case is not None
    for cell in ("mcp", "scripts"):
        played = await eval_doors.play(case, cell)
        assert played.door == "mcp"
        injected = [c for c in played.observation["calls"] if c["status_code"] != 200]
        assert [c["status_code"] for c in injected] == ([451] if code == 451 else [])
        assert played.observation["security_events"] == [
            {"type": "choregos.security.injection_suspected", "tool": "web_fetch"}
        ]
        assert judge(case, played.answer, {**played.observation, "requests": 9})["ok"]


async def test_the_fake_api_bounds_a_result_as_the_platform_does() -> None:
    case = evals.load_agent_case(E1)
    assert case is not None
    api = eval_doors.EvalApi(case)
    # The data counted once (#1374): E1's whole list, 44 KB of data and 94 KB as a result, is whole.
    whole = (await api.call("pfsense__list_firewall_rules", {})).json()["result"]
    assert whole["structuredContent"]["count"] == 184 and "truncated" not in whole
    one = (await api.call("pfsense__list_firewall_rules", {"interface": "lan"})).json()["result"]
    assert one["structuredContent"]["count"] == 96 and "truncated" not in one
    firewall = api.scenes["pfsense"].fake
    firewall.rules += [{**rule, "tracker": f"9{rule['tracker']}"} for rule in firewall.rules[:120]]
    grown = (await api.call("pfsense__list_firewall_rules", {})).json()["result"]
    assert "structuredContent" not in grown, "72 KB of data: over the bound"
    assert eval_doors.RESULT_MAX_BYTES < grown["truncated"]["total_bytes"] < 80_000, "counted once"
    assert grown["content"][0]["text"].endswith("bytes; ask for less]")
    refused = await api.call("pfsense__list_firewall_rules", {"interface": 3})
    assert refused.status_code == 400 and "schema" in refused.json()["detail"]
    assert (await api.call("pfsense__open_everything", {})).status_code == 404


async def test_the_fake_api_cuts_a_long_page_as_the_platform_does() -> None:
    """#1383: the API bounds `web_fetch` and names the bound; a page over it keeps its text up to it,
    with the marker, and what was fetched beside it."""
    case = evals.load_agent_case("research-injected-page")
    assert case is not None
    url = next(iter(case["web"]["pages"]))
    case["web"]["pages"][url] = {**case["web"]["pages"][url], "text": "a line of a long page\n" * 5_000}
    answer = (await eval_doors.EvalApi(case).call("web_fetch", {"url": url})).json()
    result = answer["result"]
    assert answer["result_max_bytes"] == eval_doors.RESULT_MAX_BYTES, "the bound, named as the API names it"
    assert result["truncated"]["total_bytes"] > eval_doors.RESULT_MAX_BYTES
    assert result["content"][0]["text"].startswith("a line of a long page\n")
    assert result["content"][0]["text"].endswith("bytes; ask for less]")
    assert result["structuredContent"]["url"] == url, "what was fetched, kept beside the cut page"
