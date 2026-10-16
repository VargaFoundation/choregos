# SPDX-License-Identifier: Apache-2.0
"""A git source cannot be wrapped in a transport: its host is checked up front, and a redirect is
refused (S39-22b, ADR 0066)."""

from __future__ import annotations

import pytest
from choregos_adapters.documents import GitSource
from choregos_adapters.documents import git as git_module
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, use_policy


class Spy:
    """Stands in for the git subprocess: records every argv, runs nothing."""

    def __init__(self) -> None:
        self.argv: list[tuple[str, ...]] = []

    async def __call__(self, cwd: object, *args: str) -> str:
        self.argv.append(args)
        return "0" * 40 + "\n" if "rev-parse" in args else ""


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    recorder = Spy()
    monkeypatch.setattr(git_module.GitSource, "_git", recorder)
    return recorder


async def test_the_fetch_forbids_redirects_and_names_its_host(spy: Spy) -> None:
    policy = EgressPolicy(org="varga", rules=(AllowedRule("github.com", used_by=("workers",)),))
    with use_policy(policy, "workers"):
        await GitSource(url="https://github.com/acme/docs.git", ref="main").changes(None)
    fetch = next(a for a in spy.argv if "fetch" in a)
    assert fetch[:2] == ("-c", "http.followRedirects=false")


async def test_a_host_absent_from_the_policy_raises_before_any_subprocess(spy: Spy) -> None:
    policy = EgressPolicy(org="varga", rules=(AllowedRule("github.com", used_by=("workers",)),))
    with use_policy(policy, "workers"), pytest.raises(EgressRefusedError) as refused:
        await GitSource(url="https://gitlab.example.test/acme/docs.git", ref="main").changes(None)
    assert refused.value.host == "gitlab.example.test"
    assert not any("fetch" in a for a in spy.argv), "git never ran the fetch"


async def test_without_a_policy_enforce_fails_closed(spy: Spy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    with pytest.raises(EgressRefusedError) as refused:
        await GitSource(url="https://github.com/acme/docs.git", ref="main").changes(None)
    assert refused.value.reason == "no_policy_in_context"
