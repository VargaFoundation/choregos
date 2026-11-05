"""The scenes of the connector fakes (S57-09): the datasets of the tool-script matrix, on the REAL
connectors, each built anew, each answer within the platform's 64 KiB bound on a tool result."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes import entra, netops
from choregos_adapters.fakes.scenes import (
    EVAL_FAKE_SCENES_ENV,
    FAKE_SCENE_FIELD,
    WEB_FIELD,
    WEB_SCENE,
    bound_scene,
    bound_web_scene,
    eval_fake_scenes_allowed,
    scene,
    scene_type,
    scenes,
)

#: The platform's bound on one result, its text and its `structuredContent` (`broker_result_max_bytes`).
BOUND = 65536


def _mcp_size(raw: dict[str, Any]) -> int:
    """What the broker measures: the MCP result it builds from a directory connector's answer."""
    text = json.dumps(raw, ensure_ascii=False, default=str)
    return len(json.dumps({"content": [{"type": "text", "text": text}], "structuredContent": raw}).encode())


async def test_the_ssh_exposure_firewall_has_the_examples_page_s_shape() -> None:
    firewall = scene("firewall", "ssh-exposure")
    interfaces = (await firewall.connector.execute("list_interfaces", {}))["interfaces"]
    aliases = await firewall.connector.execute("list_aliases", {})
    assert [i["id"] for i in interfaces] == [name for name, *_ in netops.SSH_EXPOSURE_INTERFACES]
    assert aliases["count"] == 41 and _mcp_size(aliases) < BOUND
    total = 0
    for interface, _device, _descr, count in netops.SSH_EXPOSURE_INTERFACES:
        rules = await firewall.connector.execute("list_firewall_rules", {"interface": interface})
        assert rules["count"] == count and _mcp_size(rules) < BOUND, interface
        total += count
    assert total == 184
    everything = await firewall.connector.execute("list_firewall_rules", {})
    assert everything["count"] == 184 and _mcp_size(everything) > BOUND, "whole, the list is over the bound"
    by_label: dict[str, dict[str, Any]] = {}
    for interface, *_ in netops.SSH_EXPOSURE_INTERFACES:
        rules = (await firewall.connector.execute("list_firewall_rules", {"interface": interface}))["rules"]
        by_label |= {f"{interface}#{n}": rule for n, rule in enumerate(rules, start=1)}
    assert by_label["wan#17"]["descr"] == "Temporary vendor SSH"
    assert (
        by_label["opt2#4"]["source"] == "PARTNERS" and by_label["lan#88"]["destination_port"] == "MGMT_PORTS"
    )
    assert by_label["opt3#7"]["disabled"] and by_label["wan#30"]["type"] == "block"
    assert set(netops.SSH_DECOYS) <= set(by_label)


async def test_the_dropped_flows_cluster_answers_each_namespace_within_the_bound() -> None:
    cluster = scene("network_policy", "dropped-flows")
    flows = netops.dropped_flows_trace().lines
    assert len(flows) == 2400
    assert sum(1 for line in flows if line["flow"]["verdict"] == "DROPPED") == 132
    for namespace in netops.DROPPED_FLOWS_NAMESPACES:
        answer = await cluster.connector.execute(
            "get_flows", {"namespace": namespace, "verdict": "DROPPED", "last": 2000}
        )
        assert _mcp_size(answer) < BOUND, namespace
    policies = await cluster.connector.execute("list_network_policies", {"namespace": "payments"})
    assert [p["name"] for p in policies["policies"]] == ["payments-ingress"]


async def test_a_scene_is_built_anew_each_time() -> None:
    first, second = scene("identity", "offboarding"), scene("identity", "offboarding")
    await first.connector.execute("disable_user", {"upn": entra.OFFBOARDING_UPN})
    assert not next(iter(first.fake.accounts.values()))["accountEnabled"]
    assert next(iter(second.fake.accounts.values()))["accountEnabled"], "the other scene is untouched"
    [account] = second.fake.accounts
    assert entra.state(second.fake)["groups"] == {group: [account] for group in entra.OFFBOARDING_GROUPS}


def test_the_scenes_name_their_kinds_and_refuse_the_unknown() -> None:
    assert scenes()["firewall"] == ["default", "ssh-exposure"]
    assert {op.name for op in scene("marketdata").operations} == {"get_bars", "get_instrument"}
    with pytest.raises(AdapterError, match="no scene for the kind"):
        scene("tracker")
    with pytest.raises(AdapterError, match="has no scene"):
        scene("firewall", "open-everything")


# ───────────────────── a connector bound to a scene, for the live matrix (S57-11a) ─────────────────────


@pytest.fixture
def eval_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")


async def test_a_connector_bound_to_a_scene_is_served_by_it_where_the_deployment_allows_eval_fakes(
    eval_fakes: None,
) -> None:
    bound = bound_scene("firewall", {FAKE_SCENE_FIELD: "ssh-exposure"})
    assert bound is not None and bound.name == "ssh-exposure"
    assert (await bound.connector.execute("list_aliases", {}))["count"] == 41, "the scene's data"
    assert bound_scene("firewall", {"base_url": "https://pfsense.example"}) is None, "no field, no scene"
    assert bound_scene("firewall", None) is None
    with pytest.raises(AdapterError, match="has no scene"):
        bound_scene("firewall", {FAKE_SCENE_FIELD: "open-everything"})


@pytest.mark.parametrize("value", ["", "0", "off", "false"])
def test_the_field_is_ignored_where_the_deployment_does_not_allow_eval_fakes(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Off by default: the connector is then built as its type says, from a configuration with no address."""
    if value:
        monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, value)
    else:
        monkeypatch.delenv(EVAL_FAKE_SCENES_ENV, raising=False)
    assert not eval_fake_scenes_allowed()
    assert bound_scene("firewall", {FAKE_SCENE_FIELD: "ssh-exposure"}) is None


#: A case's `web:` (#1378): what the search finds, the pages it reads.
WEB = {
    "search": {"flags": [{"title": "Flags", "url": "https://flags.example.org/l", "snippet": "Apache-2.0"}]},
    "pages": {
        "https://flags.example.org/l": {"title": "Flags", "text": "Apache-2.0."},
        "https://reviews.example.net/2026": {"title": "Reviews", "text": "Compared."},
    },
}


async def test_a_web_search_connector_bound_to_a_fake_web_holds_the_case_s_results_and_pages(
    eval_fakes: None,
) -> None:
    bound = bound_web_scene({FAKE_SCENE_FIELD: WEB_SCENE, WEB_FIELD: WEB})
    assert bound is not None
    assert [hit["url"] for hit in await bound.search.search("flags")] == ["https://flags.example.org/l"]
    assert bound.pages == WEB["pages"] and bound.hosts == ["flags.example.org", "reviews.example.net"]
    again = bound_web_scene({FAKE_SCENE_FIELD: WEB_SCENE, WEB_FIELD: WEB})
    assert again is not None and again.search is not bound.search, "built anew each time"
    assert bound_web_scene({FAKE_SCENE_FIELD: "ssh-exposure"}) is None, "another scene binds no web"
    assert bound_web_scene({WEB_FIELD: WEB}) is None, "the pages alone bind nothing"
    assert bound_scene("web_search", {FAKE_SCENE_FIELD: WEB_SCENE}) is None, "the broker never serves it"


def test_a_fake_web_is_ignored_where_the_deployment_does_not_allow_eval_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV, raising=False)
    assert bound_web_scene({FAKE_SCENE_FIELD: WEB_SCENE, WEB_FIELD: WEB}) is None


@pytest.mark.parametrize(
    ("kind", "type_name"),
    [
        ("firewall", "pfsense"),
        ("identity", "entra"),
        ("mcp", "mcp"),
        ("tracker", "jira"),
        ("runtime", "fake"),
        ("web_search", "fake"),
        ("web_search", "brave"),
    ],
)
def test_no_declaration_or_edit_may_bind_a_connector_to_a_fake(
    eval_fakes: None, kind: str, type_name: str
) -> None:
    """A person, a project or an organisation never sets the field, even on a deployment that serves eval
    fakes: only the matrix's seeding, which writes its rows itself, does (`registry.validate_config`)."""
    from choregos_adapters import validate_config
    from choregos_adapters.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="fake_scene"):
        validate_config(kind, type_name, {FAKE_SCENE_FIELD: "ssh-exposure"})


def test_each_kind_with_scenes_runs_a_registered_type() -> None:
    from choregos_adapters import spec_of

    assert {kind: scene_type(kind) for kind in scenes()} == {
        "firewall": "pfsense",
        "network_policy": "cilium",
        "identity": "entra",
        "marketdata": "composite",
    }
    assert all(spec_of(kind, scene_type(kind)) is not None for kind in scenes())
    with pytest.raises(AdapterError, match="no scene for the kind"):
        scene_type("tracker")
