"""Host patterns and the effective network of a run (ADR 0070)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from choregos_core.network import (
    allowed,
    effective_network,
    host_matches,
    outside,
    pattern_within,
    repo_host,
    unsatisfied,
    validate_pattern,
)


@pytest.mark.parametrize("pattern", ["example.com", "*.example.com", "*", "API.Example.COM.", "a-b.c_d.io"])
def test_valid_patterns_are_accepted_and_normalised(pattern: str) -> None:
    assert validate_pattern(pattern) == pattern.lower().rstrip(".")


@pytest.mark.parametrize(
    "pattern",
    ["", "https://x.com", "x.com/y", "x.com:443", "user@x.com", "1.2.3.4", "::1", "*x.com", "a.*.com",
     "a..com", "*.com", "x .com", "x.*", "ex!ample.com"],
)  # fmt: skip
def test_invalid_patterns_are_refused_with_a_reason(pattern: str) -> None:
    with pytest.raises(ValueError):
        validate_pattern(pattern)


def test_a_bare_pattern_covers_subdomains_a_wildcard_does_not_cover_the_apex() -> None:
    assert host_matches("github.com", "github.com")
    assert host_matches("api.github.com", "github.com")
    assert not host_matches("notgithub.com", "github.com")
    assert not host_matches("github.com", "*.github.com")
    assert host_matches("api.github.com", "*.github.com")
    assert host_matches("anything.example", "*")


def test_an_ip_literal_matches_nothing_not_even_a_star() -> None:
    assert not host_matches("10.0.0.1", "*")
    assert allowed("10.0.0.1", ["*"]) == (False, "ip_literal")
    assert allowed("[::1]", ["*"]) == (False, "ip_literal")


def test_deny_wins_over_allow_and_the_reason_says_which_rule() -> None:
    assert allowed("api.github.com", ["github.com"], ["api.github.com"]) == (False, "denied:api.github.com")
    assert allowed("github.com", ["github.com"], ["api.github.com"]) == (True, "allowed:github.com")
    assert allowed("pypi.org", ["github.com"]) == (False, "not_allowed")
    assert allowed("GitHub.com.", ["github.com"]) == (True, "allowed:github.com")
    assert allowed("anything.example", [], ["*"]) == (False, "denied:*")


@pytest.mark.parametrize(
    ("inner", "outer", "within"),
    [
        ("api.github.com", "github.com", True),
        ("*.github.com", "github.com", True),
        ("github.com", "github.com", True),
        ("github.com", "*.github.com", False),
        ("api.github.com", "*.github.com", True),
        ("*.api.github.com", "*.github.com", True),
        ("x.org", "*", True),
        ("*", "*", True),
        ("*", "github.com", False),
        ("evilgithub.com", "github.com", False),
    ],
)
def test_pattern_within_decides_what_only_narrows(inner: str, outer: str, within: bool) -> None:
    assert pattern_within(inner, outer) is within


def test_outside_lists_what_a_cap_does_not_cover_and_none_is_no_cap() -> None:
    assert outside(["a.com", "b.org"], ["a.com"]) == ["b.org"]
    assert outside(["a.com", "b.org"], None) == []
    assert outside(["a.com"], []) == ["a.com"]


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://github.com/o/r.git", "github.com"),
        ("git@gitlab.example.com:o/r.git", "gitlab.example.com"),
        ("ssh://git@git.example.com:2222/o/r", "git.example.com"),
        ("github.com/o/r", "github.com"),
        ("", None),
        (None, None),
    ],
)
def test_repo_host(url: str | None, host: str | None) -> None:
    assert repo_host(url) == host


@dataclass
class Net:
    allow_domains: list[str] = field(default_factory=list)
    deny_domains: list[str] = field(default_factory=list)
    per_host_per_minute: int | None = None
    per_run_per_minute: int | None = None
    builtin_web_tools: str = "allow"


@dataclass
class AgentNet:
    domains: list[str] | None = None
    per_host_per_minute: int | None = None
    builtin_web_tools: bool | None = None


@dataclass
class CeilingNet:
    allow_domains: list[str] | None = None
    deny_domains: list[str] = field(default_factory=list)
    per_host_per_minute: int | None = None
    per_run_per_minute: int | None = None
    builtin_web_tools: str | None = None


PROJECT = Net(allow_domains=["github.com", "pypi.org", "files.pythonhosted.org"])


def test_an_agent_without_a_network_inherits_the_projects_list() -> None:
    eff = effective_network(PROJECT, AgentNet(), None)
    assert eff.allow == ["github.com", "pypi.org", "files.pythonhosted.org"]
    assert effective_network(PROJECT, None, None).allow == eff.allow


def test_an_empty_agent_list_means_no_internet_and_a_list_narrows() -> None:
    assert effective_network(PROJECT, AgentNet(domains=[]), None).allow == []
    eff = effective_network(PROJECT, AgentNet(domains=["pypi.org", "api.github.com"]), None)
    assert eff.allow == ["pypi.org", "api.github.com"]


def test_an_agent_host_the_project_does_not_allow_is_never_part_of_the_network() -> None:
    eff = effective_network(PROJECT, AgentNet(domains=["pypi.org", "api.stripe.com"]), None)
    assert eff.allow == ["pypi.org"]


def test_the_ceiling_caps_the_projects_list_and_its_deny_always_wins() -> None:
    ceiling = CeilingNet(allow_domains=["github.com", "pypi.org"], deny_domains=["gist.github.com"])
    eff = effective_network(PROJECT, None, ceiling)
    assert eff.allow == ["github.com", "pypi.org"], "files.pythonhosted.org is outside the cap"
    assert eff.deny == ["gist.github.com"]
    assert allowed("gist.github.com", eff.allow, eff.deny)[0] is False
    assert allowed("api.github.com", eff.allow, eff.deny)[0] is True


def test_a_deny_covering_an_allowed_pattern_removes_it_and_the_instance_deny_merges() -> None:
    ceiling = CeilingNet(deny_domains=["pypi.org"])
    eff = effective_network(PROJECT, None, ceiling, instance_deny=["evil.example"])
    assert "pypi.org" not in eff.allow
    assert eff.deny == ["evil.example", "pypi.org"]


def test_the_project_deny_list_is_merged_too() -> None:
    eff = effective_network(Net(allow_domains=["a.com"], deny_domains=["b.a.com"]), None, None)
    assert eff.deny == ["b.a.com"]


def test_rates_take_the_minimum_and_the_organisations_cap_is_reported_apart() -> None:
    policy = Net(allow_domains=["a.com"], per_host_per_minute=30, per_run_per_minute=500)
    eff = effective_network(
        policy, AgentNet(per_host_per_minute=10), CeilingNet(per_host_per_minute=60, per_run_per_minute=200)
    )
    assert (eff.per_host_per_minute, eff.per_run_per_minute, eff.org_per_host_per_minute) == (10, 200, 60)
    assert effective_network(Net(), None, None).per_host_per_minute is None


@pytest.mark.parametrize(
    ("policy", "agent", "ceiling", "expected"),
    [
        ("allow", None, None, True),
        ("deny", None, None, False),
        ("allow", False, None, False),
        ("allow", True, None, True),
        ("allow", None, "deny", False),
        ("allow", True, "deny", False),
        ("allow", None, "allow", True),
    ],
)
def test_built_in_web_tools_are_on_only_when_nobody_switches_them_off(
    policy: str, agent: bool | None, ceiling: str | None, expected: bool
) -> None:
    eff = effective_network(
        Net(builtin_web_tools=policy),
        AgentNet(builtin_web_tools=agent),
        CeilingNet(builtin_web_tools=ceiling),
    )
    assert eff.builtin_web_tools is expected


def test_the_repository_host_is_implied_unless_denied() -> None:
    eff = effective_network(Net(), None, None, implied_hosts=["github.com"])
    assert eff.implied == ["github.com"]
    assert eff.allow == []
    assert eff.reachable == ["github.com"]
    denied = effective_network(
        Net(), None, CeilingNet(deny_domains=["github.com"]), implied_hosts=["github.com"]
    )
    assert denied.implied == []


def test_unsatisfied_names_exactly_the_missing_hosts_and_why() -> None:
    ceiling = CeilingNet(
        allow_domains=["github.com", "pypi.org", "stripe.com"], deny_domains=["gist.github.com"]
    )
    lines = unsatisfied(
        AgentNet(domains=["pypi.org", "api.stripe.com", "gist.github.com", "example.org", "1.2.3.4"]),
        PROJECT,
        ceiling,
    )
    joined = "\n".join(lines)
    assert "`pypi.org`" not in joined
    assert "`api.stripe.com` is not in the project's" in joined
    assert "`gist.github.com` is denied by the organisation" in joined
    assert "`example.org` is outside the organisation's allowed hosts" in joined
    assert "1.2.3.4" in joined
    assert len(lines) == 4


def test_unsatisfied_without_a_project_only_checks_the_organisation() -> None:
    ceiling = CeilingNet(allow_domains=["github.com"])
    lines = unsatisfied(AgentNet(domains=["github.com", "stripe.com"]), None, ceiling)
    assert lines == ["`stripe.com` is outside the organisation's allowed hosts"]
    assert unsatisfied(AgentNet(domains=None), PROJECT, ceiling) == []
    assert unsatisfied(AgentNet(domains=[]), PROJECT, ceiling) == []


def test_the_repository_host_satisfies_an_agent_that_names_it() -> None:
    assert (
        unsatisfied(AgentNet(domains=["git.example.com"]), Net(), None, implied_hosts=["git.example.com"])
        == []
    )
    assert unsatisfied(AgentNet(domains=["git.example.com"]), Net(), None) != []
