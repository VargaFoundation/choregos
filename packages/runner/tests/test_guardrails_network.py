"""The runner's guardrails read the run's network (ADR 0070): a net, with a useful message."""

from __future__ import annotations

from typing import Any

from choregos_contracts import Permissions
from choregos_runner.guardrails import GuardRails


def rails(**kwargs: Any) -> GuardRails:
    return GuardRails(Permissions(**kwargs))


def test_unlisted_with_no_list_keeps_todays_behaviour() -> None:
    decision = rails().check_network("https://anywhere.example/x")
    assert decision.allowed and "sandbox filters" in decision.reason


def test_listed_with_no_list_means_no_internet_and_says_where_to_change_it() -> None:
    decision = rails(egress="listed").check_network("https://pypi.org/simple")
    assert not decision.allowed
    assert "no internet for this run" in decision.reason and "pypi.org" in decision.reason
    assert "Network section" in decision.reason and "policy ceiling" in decision.reason


def test_a_listed_host_and_its_subdomains_pass_and_others_are_refused_with_the_list() -> None:
    guard = rails(egress="listed", allow_domains=["github.com", "pypi.org"])
    assert guard.check_network("https://github.com/o/r").allowed
    assert guard.check_network("https://api.github.com/x").allowed
    refused = guard.check_network("https://stripe.com/v1")
    assert not refused.allowed
    assert "stripe.com" in refused.reason and "github.com, pypi.org" in refused.reason
    assert not guard.check_network("https://notgithub.com").allowed


def test_the_deny_list_wins_even_over_an_allowed_host() -> None:
    guard = rails(egress="listed", allow_domains=["github.com"], deny_domains=["gist.github.com"])
    assert guard.check_network("https://api.github.com").allowed
    denied = guard.check_network("https://gist.github.com/x")
    assert not denied.allowed and "denied" in denied.reason and "gist.github.com" in denied.reason


def test_an_unlisted_run_with_a_deny_list_but_no_allowlist_refuses_only_the_denied() -> None:
    guard = rails(deny_domains=["x.example"])
    assert guard.check_network("https://fine.example").allowed
    assert not guard.check_network("https://sub.x.example").allowed


def test_an_ip_literal_is_refused_when_a_list_is_in_force() -> None:
    guard = rails(egress="listed", allow_domains=["*"])
    assert not guard.check_network("http://169.254.169.254/latest").allowed
    assert guard.check_network("https://anything.example").allowed


def test_the_url_goes_through_decide_too() -> None:
    guard = rails(egress="listed", allow_domains=["github.com"])
    ok = guard.decide({"toolCall": {"kind": "fetch", "rawInput": {"url": "https://github.com/x"}}})
    no = guard.decide({"toolCall": {"kind": "fetch", "rawInput": {"url": "https://evil.example/x"}}})
    assert ok.allowed and not no.allowed and no.kind == "network"


def test_websearch_has_no_url_and_is_refused_when_built_in_web_tools_are_off() -> None:
    off = rails(builtin_web_tools=False)
    search = off.decide({"toolCall": {"title": "WebSearch: choregos agents", "rawInput": {"query": "x"}}})
    assert not search.allowed and "built-in web tools" in search.reason
    fetch = off.decide(
        {"toolCall": {"title": "WebFetch https://github.com", "rawInput": {"url": "https://github.com"}}}
    )
    assert not fetch.allowed and "built-in web tools" in fetch.reason


def test_websearch_is_allowed_when_the_built_in_web_tools_are_on() -> None:
    on = rails(builtin_web_tools=True)
    assert on.decide(
        {"toolCall": {"title": "WebSearch: choregos agents", "rawInput": {"query": "x"}}}
    ).allowed


def test_switching_the_web_tools_off_does_not_block_other_requests() -> None:
    off = rails(builtin_web_tools=False, write_paths=["src/**"])
    assert off.decide({"toolCall": {"kind": "read", "rawInput": {"path": "README.md"}}}).allowed
    assert off.decide(
        {"toolCall": {"title": "Write /workspace/src/a.py", "rawInput": {"path": "src/a.py", "content": "x"}}}
    ).allowed
