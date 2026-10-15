"""The network section of the organisation's ceiling (ADR 0070): widenings, clamps."""

from __future__ import annotations

import pytest
from choregos_contracts import Policy
from choregos_core import load_preset, policy_warnings
from choregos_core.policy_ceiling import PolicyCeiling, clamp, widenings
from pydantic import ValidationError


def _policy(**network: object) -> Policy:
    policy = load_preset("solo").model_copy(deep=True)
    for key, value in network.items():
        setattr(policy.sandbox.network, key, value)
    return policy


def _ceiling(**network: object) -> PolicyCeiling:
    return PolicyCeiling.model_validate({"network": network})


def test_an_allowed_host_outside_the_ceilings_set_is_a_widening() -> None:
    ceiling = _ceiling(allow_domains=["github.com", "pypi.org"])
    said = widenings(_policy(allow_domains=["github.com", "api.stripe.com"]), ceiling)
    assert said == ["network.allow_domains: `api.stripe.com` is outside the organisation's allowed hosts"]
    assert widenings(_policy(allow_domains=["api.github.com"]), ceiling) == []


def test_no_cap_means_any_host_and_star_covers_everything() -> None:
    assert widenings(_policy(allow_domains=["anything.example"]), _ceiling()) == []
    assert widenings(_policy(allow_domains=["anything.example"]), _ceiling(allow_domains=["*"])) == []


def test_a_host_the_ceiling_denies_is_a_widening() -> None:
    said = widenings(_policy(allow_domains=["github.com", "x.example"]), _ceiling(deny_domains=["x.example"]))
    assert said == ["network.allow_domains: `x.example` is denied by the organisation (`x.example`)"]


def test_a_rate_above_the_cap_and_no_rate_under_a_cap_are_widenings_and_numbers_read_as_before() -> None:
    ceiling = _ceiling(per_host_per_minute=60, per_run_per_minute=300)
    said = widenings(_policy(per_host_per_minute=120, per_run_per_minute=300), ceiling)
    assert said == ["network.per_host_per_minute: 120 exceeds the organisation's ceiling of 60"]
    unlimited = widenings(_policy(), ceiling)
    assert unlimited == [
        "network.per_host_per_minute: unlimited exceeds the organisation's ceiling of 60",
        "network.per_run_per_minute: unlimited exceeds the organisation's ceiling of 300",
    ]


def test_allow_web_tools_under_a_deny_ceiling_is_a_widening() -> None:
    ceiling = _ceiling(builtin_web_tools="deny")
    assert len(widenings(_policy(builtin_web_tools="allow"), ceiling)) == 1
    assert widenings(_policy(builtin_web_tools="deny"), ceiling) == []
    assert widenings(_policy(builtin_web_tools="allow"), _ceiling(builtin_web_tools="allow")) == []


def test_clamp_drops_caps_and_unions_the_deny_list() -> None:
    policy = _policy(
        allow_domains=["github.com", "api.stripe.com", "x.example"],
        deny_domains=["mine.example"],
        per_host_per_minute=500,
        builtin_web_tools="allow",
    )
    ceiling = _ceiling(
        allow_domains=["github.com", "x.example"],
        deny_domains=["x.example", "tracker.example"],
        per_host_per_minute=60,
        builtin_web_tools="deny",
    )
    clamped, said = clamp(policy, ceiling)
    net = clamped.sandbox.network
    assert net.allow_domains == ["github.com"]
    assert net.deny_domains == ["mine.example", "x.example", "tracker.example"]
    assert net.per_host_per_minute == 60
    assert net.builtin_web_tools == "deny"
    assert len(said) == 4
    assert policy.sandbox.network.per_host_per_minute == 500, "the stored policy is not touched"


def test_clamp_merges_the_deny_list_even_when_nothing_else_is_wrong() -> None:
    clamped, said = clamp(_policy(), _ceiling(deny_domains=["tracker.example"]))
    assert clamped.sandbox.network.deny_domains == ["tracker.example"]
    assert said == []


def test_the_ceiling_refuses_a_bad_pattern_and_a_zero_rate() -> None:
    with pytest.raises(ValidationError, match="IP address"):
        _ceiling(deny_domains=["10.0.0.1"])
    with pytest.raises(ValidationError, match="scheme"):
        _ceiling(allow_domains=["https://github.com"])
    with pytest.raises(ValidationError):
        _ceiling(per_host_per_minute=0)
    assert _ceiling(allow_domains=["GitHub.com."]).network.allow_domains == ["github.com"]


def test_deny_by_default_false_is_flagged_as_not_enforced_and_bad_patterns_as_ignored() -> None:
    policy = Policy.model_validate(
        {
            "apiVersion": "choregos/v1",
            "kind": "Policy",
            "metadata": {"name": "p", "version": 1},
            "sandbox": {"network": {"deny_by_default": False, "allow_domains": ["https://x.com"]}},
        }
    )
    codes = {issue.code for issue in policy_warnings(policy)}
    assert codes == {"policy.network_deny_by_default_not_enforced", "policy.network_pattern_ignored"}
    assert policy_warnings(load_preset("solo")) == []


def test_presets_let_pip_and_github_downloads_through() -> None:
    for name in ("solo", "team"):
        hosts = load_preset(name).sandbox.network.allow_domains
        assert "files.pythonhosted.org" in hosts
        assert "githubusercontent.com" in hosts
    assert load_preset("regulated").sandbox.network.allow_domains == []
    assert load_preset("regulated").sandbox.network.per_host_per_minute == 60
    assert load_preset("regulated").sandbox.network.builtin_web_tools == "deny"
    assert load_preset("team").sandbox.network.builtin_web_tools == "deny"
    assert load_preset("solo").sandbox.network.builtin_web_tools == "allow"
