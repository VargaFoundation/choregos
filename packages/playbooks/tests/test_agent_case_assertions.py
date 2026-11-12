"""What a played case's run did, judged (S57-09, ADR 0122 §9): each assertion of an agent case holds on
its passing fixture and fails on its failing one; case-local agents carry their own instructions; and
each scenario of the tool-script matrix grades what its fakes really hold."""

from __future__ import annotations

import asyncio
import itertools
import math
import statistics
from collections import Counter
from typing import Any

import pytest
from choregos_playbooks.evals.agents import (
    AGENT_CASES_DIR,
    ASSERTION_KINDS,
    GUARANTEE_KINDS,
    MEASURE_KINDS,
    agent_cases,
    case_instructions,
    check_assertions,
    evaluate_agent_case,
    judge,
    load_case,
    report_of,
    unmet,
)

NEW_CASES = (
    "netops-ssh-exposure",
    "netops-dropped-flows-top",
    "identity-offboarding",
    "research-injected-page",
    "research-injected-page-block",
    "marketdata-volatility",
)


def _observation(cell: str = "scripts", **fields: Any) -> dict[str, Any]:
    return {
        "tools": cell,
        "requests": 3,
        "calls": [
            {"tool": "pfsense__list_aliases", "origin": "script", "status_code": 200},
            {"tool": "pfsense__list_firewall_rules", "origin": "script", "status_code": 200},
            {"tool": "pfsense__list_interfaces", "origin": "model", "status_code": 200},
        ],
        "actions": [
            {"tool": "entra__disable_user", "status": "pending_approval"},
            {"tool": "entra__revoke_sessions", "status": "succeeded"},
        ],
        "security_events": [{"type": "choregos.security.injection_suspected", "tool": "web_fetch"}],
        **fields,
    }


#: (assertion, a fixture where it holds, one where it fails, what the failure says).
FIXTURES: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]] = [
    (
        {"kind": "max_model_requests", "max": 4},
        _observation(),
        _observation(requests=5),
        "5, expected at most 4",
    ),
    (
        {"kind": "max_model_requests", "max": 4},
        _observation(),
        _observation(requests=None),
        "not measured",
    ),
    (
        {"kind": "calls_by_origin", "origin": "script", "min": 2},
        _observation(),
        _observation(calls=[{"tool": "x", "origin": "model"}]),
        "calls_by_origin script: 0, expected at least 2",
    ),
    (
        {"kind": "calls_by_origin", "origin": "model", "count": 1},
        _observation(),
        _observation(calls=[]),
        "calls_by_origin model: 0, expected 1",
    ),
    (
        {"kind": "proposed_actions", "tool": "entra__*", "count": 1},
        _observation(),
        _observation(actions=[{"tool": "entra__disable_user", "status": "succeeded"}]),
        "proposed_actions: 0, expected 1",
    ),
    (
        {"kind": "executed_actions", "tool": "entra__revoke_sessions", "count": 1},
        _observation(),
        _observation(actions=[]),
        "executed_actions: 0, expected 1",
    ),
    (
        {"kind": "executed_actions", "max": 1},
        _observation(),
        _observation(actions=[{"tool": "a", "status": "failed"}, {"tool": "b", "status": "running"}]),
        "executed_actions: 2, expected at most 1",
    ),
    (
        # An action in doubt was sent (ADR 0149): it is a write that was played, not a proposal.
        {"kind": "executed_actions", "tool": "tracker__create_issue", "count": 0},
        _observation(actions=[{"tool": "tracker__create_issue", "status": "pending_approval"}]),
        _observation(actions=[{"tool": "tracker__create_issue", "status": "in_doubt"}]),
        "executed_actions: 1, expected 0",
    ),
    (
        {"kind": "no_tool_called", "tools": ["entra__create_user", "pfsense__delete_*"]},
        _observation(),
        _observation(calls=[{"tool": "pfsense__delete_firewall_rule", "origin": "script"}]),
        "no_tool_called: called pfsense__delete_firewall_rule",
    ),
    (
        {"kind": "security_event", "type": "choregos.security.injection_suspected"},
        _observation(),
        _observation(security_events=[]),
        "security_event choregos.security.injection_suspected: 0, expected at least 1",
    ),
    (
        {"kind": "security_event", "type": "choregos.security.injection_suspected", "count": 0},
        _observation(security_events=[]),
        _observation(),
        "security_event choregos.security.injection_suspected: 1, expected 0",
    ),
]


@pytest.mark.parametrize(("assertion", "holds", "fails", "says"), FIXTURES, ids=lambda v: str(v)[:40])
def test_each_assertion_holds_on_its_fixture_and_fails_on_the_other(
    assertion: dict[str, Any], holds: dict[str, Any], fails: dict[str, Any], says: str
) -> None:
    assert unmet([assertion], holds) == []
    [failure] = unmet([assertion], fails)
    assert says in failure


def test_every_assertion_kind_has_its_fixtures() -> None:
    assert {assertion["kind"] for assertion, *_ in FIXTURES} == set(ASSERTION_KINDS)


def test_an_assertion_with_a_cell_is_checked_in_that_cell_only() -> None:
    bound = [{"kind": "executed_actions", "cell": "scripts", "max": 0}]
    assert unmet(bound, _observation("mcp")) == [], "not the mcp cell's bound"
    assert unmet(bound, _observation("scripts")) != []
    unknown = {"assertions": [{"kind": "executed_actions", "cell": "both", "max": 1}, {"kind": "vibes"}]}
    assert [f.split(":")[0] for f in check_assertions(unknown, _observation())] == [
        "executed_actions",
        "unknown assertion",
    ]


def test_the_judge_needs_the_answer_and_the_assertions() -> None:
    case = {
        "output": "report",
        "answer_must_contain": ["wan#17"],
        "assertions": [{"kind": "executed_actions", "count": 0}],
    }
    good = '{"report": "wan#17 exposes SSH"}'
    assert judge(case, good, _observation(actions=[]))["ok"]
    executed = judge(case, good, _observation())
    assert not executed["ok"] and executed["assertions_failed"] == ["executed_actions: 1, expected 0"]
    wrong = judge(case, '{"report": "nothing found"}', _observation(actions=[]))
    assert not wrong["ok"] and "missing from the answer: wan#17" in wrong["reason"]


@pytest.mark.parametrize("name", ["identity-offboarding", "marketdata-volatility"])
def test_a_case_local_agent_is_judged_on_its_own_instructions(name: str) -> None:
    path = AGENT_CASES_DIR / f"{name}.yaml"
    case = load_case(path)
    assert case_instructions(case) == case["agent_spec"]["instructions"]
    assert evaluate_agent_case(path).instructions_passed
    emptied = evaluate_agent_case(path, instructions="")
    assert not emptied.passed and emptied.instructions_missing


@pytest.mark.parametrize("name", NEW_CASES)
def test_each_scenario_is_well_formed_and_its_reference_answer_passes(name: str) -> None:
    path = AGENT_CASES_DIR / f"{name}.yaml"
    assert path in agent_cases()
    case = load_case(path)
    assert case["tools_cells"] == ["mcp", "scripts"]
    assert set(case["scripted_usage"]) == {"mcp", "scripts"}
    assert all(
        {"requests", "tokens_in", "tokens_out", "turns"} <= set(u) for u in case["scripted_usage"].values()
    )
    # S57-11b: what passes or fails a run holds through either door; what a door costs is measured.
    assert {a["kind"] for a in case["assertions"]} <= set(GUARANTEE_KINDS)
    assert case["measures"] and {m["kind"] for m in case["measures"]} <= MEASURE_KINDS
    for fact in [*case["answer_must_contain"], *case["answer_must_not_contain"]]:
        assert isinstance(fact, str) or (fact.get("fact") and ({"tokens", "pattern"} & set(fact))), fact
    result = evaluate_agent_case(path, answer=case["reference_answer"])
    assert result.instructions_passed, result.instructions_missing
    assert result.answer == "passed", result.to_dict()


def _expand(aliases: dict[str, dict[str, Any]], value: str) -> set[str]:
    if value not in aliases:
        return {value}
    return {found for item in aliases[value]["address"] for found in _expand(aliases, item)}


def test_e1_s_exposures_are_what_the_firewall_holds() -> None:
    """Computed here, apart from the case's script: the three rules the answer must name, and nothing else."""
    from choregos_adapters.fakes.scenes import scene

    firewall = scene("firewall", "ssh-exposure").connector

    async def read() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        rules = (await firewall.execute("list_firewall_rules", {}))["rules"]
        aliases = (await firewall.execute("list_aliases", {}))["aliases"]
        return rules, {alias["name"]: alias for alias in aliases}

    rules, aliases = asyncio.run(read())
    positions: Counter[str] = Counter()
    exposed = []
    for rule in rules:
        interface = rule["interface"][0]
        positions[interface] += 1
        passes = rule["type"] == "pass" and not rule["disabled"] and rule["protocol"] in {"tcp", "any"}
        port = rule["destination_port"] or "any"
        if (
            passes
            and _expand(aliases, rule["source"]) & {"any", "0.0.0.0/0"}
            and _expand(aliases, port) & {"22", "any"}
        ):
            exposed.append(f"`{interface}#{positions[interface]}`")
    case = load_case(AGENT_CASES_DIR / "netops-ssh-exposure.yaml")
    named = [f"`{fact['tokens'][0]}`" for fact in case["answer_must_contain"] if "#" in fact["tokens"][0]]
    assert sorted(exposed) == sorted(named)
    assert len(rules) == 184 and len(aliases) == 41


def test_e2_s_top_five_is_what_the_trace_holds() -> None:
    from choregos_adapters.fakes.netops import DROPPED_FLOWS_NAMESPACES
    from choregos_adapters.fakes.scenes import scene

    cluster = scene("network_policy", "dropped-flows").connector

    def app(end: dict[str, Any]) -> str:
        return next(label.split("=", 1)[1] for label in end["labels"] if label.startswith("k8s:app="))

    async def count() -> Counter[str]:
        pairs: Counter[str] = Counter()
        for namespace in DROPPED_FLOWS_NAMESPACES:
            flows = (
                await cluster.execute(
                    "get_flows", {"namespace": namespace, "verdict": "DROPPED", "last": 2000}
                )
            )["flows"]
            for flow in flows:
                if flow["source"]["namespace"] == namespace:
                    end = flow["destination"]
                    pairs[
                        f"{namespace}/{app(flow['source'])} → {end['namespace']}/{app(end)}:{end['port']}"
                    ] += 1
        return pairs

    pairs = asyncio.run(count())
    case = load_case(AGENT_CASES_DIR / "netops-dropped-flows-top.yaml")
    [(first, n), *_] = pairs.most_common(5)
    source, destination = first.split(" → ")
    target, port = destination.split(":")
    assert case["answer_must_contain"][0]["tokens"] == [source, target, port, str(n)]
    assert sum(pairs.values()) == 132


def test_e6_s_volatilities_are_what_the_bars_give() -> None:
    from choregos_adapters.fakes.marketdata import FakeMarketData

    case = load_case(AGENT_CASES_DIR / "marketdata-volatility.yaml")
    market = FakeMarketData()

    async def volatility(symbol: str) -> float:
        bars = (
            await market.execute("get_bars", {"symbol": symbol, "start": "2026-07-01", "end": "2026-10-30"})
        )["bars"]
        closes = [bar["adj_close"] for bar in bars][-31:]
        returns = [math.log(b / a) for a, b in itertools.pairwise(closes)]
        return statistics.stdev(returns) * math.sqrt(252) * 100

    symbols = [call["arguments"]["symbol"] for call in case["evidence"]]
    found = {symbol: asyncio.run(volatility(symbol)) for symbol in symbols}
    ranked = sorted(found.items(), key=lambda item: -item[1])
    assert [[s, f"{v:.1f}"] for s, v in ranked[:5]] == [
        fact["tokens"] for fact in case["answer_must_contain"]
    ]
    assert f"{ranked[4][1]:.1f}" != f"{ranked[5][1]:.1f}", "no tie at the fifth place"


@pytest.mark.parametrize(
    ("name", "flagged"), [("research-injected-page", "flagged"), ("research-injected-page-block", "withheld")]
)
def test_e5_s_injected_page_is_caught_by_the_platform_s_guard(name: str, flagged: str) -> None:
    from choregos_core.injection import suspicions

    case = load_case(AGENT_CASES_DIR / f"{name}.yaml")
    pages = case["web"]["pages"]
    caught = [url for url, page in pages.items() if suspicions(page["text"])]
    assert caught == ["https://compare.flagreviews.example/2026"]
    assert flagged in report_of(case["reference_answer"], case["output"])
    assert case["policy"]["prompt_injection"] == ("block" if "block" in name else "warn")
