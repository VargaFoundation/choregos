"""The organisation's network bounds (ADR 0070) ride in its egress policy (ADR 0066): one decision
for every call the platform makes, whatever the egress mode (ADR 0070, amended at integration)."""

from __future__ import annotations

import pytest
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, Mode, NetworkBounds
from choregos_core.network import platform_allowed

OPENED = (AllowedRule("mcp.example.test"), AllowedRule("*.wide.example.test"))


@pytest.mark.parametrize("mode", ["enforce", "warn", "off"])
def test_a_denied_host_is_refused_in_every_mode_even_when_opened(mode: Mode) -> None:
    bounds = NetworkBounds(deny=("mcp.example.test",))
    policy = EgressPolicy(org="varga", rules=OPENED, mode=mode, bounds=bounds)
    with pytest.raises(EgressRefusedError) as refused:
        policy.check("mcp.example.test")
    assert refused.value.reason == "network_policy"
    assert refused.value.detail == "denied:mcp.example.test"
    assert "network policy of organisation varga" in str(refused.value)


def test_outside_the_allowed_set_is_refused_and_inside_follows_the_list() -> None:
    policy = EgressPolicy(rules=OPENED, mode="enforce", bounds=NetworkBounds(allow=("wide.example.test",)))
    with pytest.raises(EgressRefusedError, match="not_allowed"):
        policy.check("mcp.example.test")
    assert policy.check("a.wide.example.test").ok, "inside the bounds, the opened rule decides"
    with pytest.raises(EgressRefusedError) as absent:
        policy.check("b.example.test")
    assert absent.value.reason == "network_policy", "the bounds are read first"


def test_without_bounds_the_list_alone_decides_as_before() -> None:
    policy = EgressPolicy(rules=OPENED, mode="enforce")
    assert policy.check("mcp.example.test").ok
    with pytest.raises(EgressRefusedError) as refused:
        policy.check("other.example.test")
    assert refused.value.reason == "org_absent"


def test_a_wildcard_is_refused_when_a_deny_covers_it() -> None:
    bounds = NetworkBounds(deny=("example.test",))
    assert bounds.refusal("*.wide.example.test") == "denied:example.test"
    assert NetworkBounds(deny=("a.wide.example.test",)).refusal("*.wide.example.test") is None
    assert NetworkBounds(allow=("other.test",)).refusal("*.wide.example.test") == "not_allowed"


def test_an_internal_host_is_not_capped_but_a_deny_naming_it_refuses_it() -> None:
    assert platform_allowed("pfsense", ["vendor.example"]) == (True, "internal")
    assert platform_allowed("10.0.0.7", ["vendor.example"]) == (True, "internal")
    assert platform_allowed("pfsense", None, ["pfsense"]) == (False, "denied")
    assert platform_allowed("api.vendor.example", ["vendor.example"]) == (True, "allowed:vendor.example")
    assert platform_allowed("evil.example", None, ["evil.example"]) == (False, "denied:evil.example")
