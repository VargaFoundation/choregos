# SPDX-License-Identifier: Apache-2.0
"""The organisation's egress matcher (S39-22a, ADR 0066): pure, no I/O."""

from __future__ import annotations

import pytest
from choregos_core.egress import (
    AllowedRule,
    EgressPolicy,
    EgressRefusedError,
    ceiling_from,
    check_current,
    current_scope,
    default_mode,
    host_matches,
    normalize_host,
    use_policy,
)


def _policy(*rules: AllowedRule, ceiling=None, mode="enforce", requested=()) -> EgressPolicy:
    return EgressPolicy(
        org="varga", rules=rules, requested=tuple(requested), ceiling=ceiling or {}, mode=mode
    )


def test_wildcard_matches_subdomains_only() -> None:
    assert host_matches("*.notion.com", "mcp.notion.com")
    assert host_matches("*.notion.com", "a.b.notion.com")
    assert not host_matches("*.notion.com", "notion.com")
    assert not host_matches("*.notion.com", "evilnotion.com")
    assert not host_matches("*.notion.com", "notion.com.evil.test")
    assert host_matches("mcp.notion.com", "mcp.notion.com")
    assert not host_matches("mcp.notion.com", "x.mcp.notion.com")


def test_normalisation_lowercases_and_idna_encodes() -> None:
    assert normalize_host(" MCP.Notion.COM. ") == "mcp.notion.com"
    assert normalize_host("bücher.example") == "xn--bcher-kva.example"
    assert normalize_host("*.Example.com", allow_wildcard=True) == "*.example.com"
    assert normalize_host("10.0.0.7") == "10.0.0.7"
    for bad in ("*.example.com", "a/b", "user@host", "a b", "", "host:8080"):
        with pytest.raises(ValueError):
            normalize_host(bad)
    with pytest.raises(ValueError):
        normalize_host("*.10.0.0.7", allow_wildcard=True)


def test_ports_and_families_must_match() -> None:
    policy = _policy(AllowedRule("mcp.example.test", (443, 8443), ("api",)))
    assert policy.verdict("mcp.example.test", 443, "api").ok
    assert policy.verdict("mcp.example.test", 8443, "api").ok
    assert policy.verdict("mcp.example.test", 80, "api").org == "absent"
    assert policy.verdict("mcp.example.test", 443, "workers").org == "absent"


def test_requested_is_not_allowed() -> None:
    rule = AllowedRule("mcp.example.test")
    policy = _policy(requested=(rule,))
    verdict = policy.verdict("mcp.example.test")
    assert verdict.org == "requested" and not verdict.ok and verdict.reason == "org_requested"


def test_empty_ceiling_family_is_unknown_and_outside_beats_allowed() -> None:
    rule = AllowedRule("mcp.example.test")
    unknown = _policy(rule, ceiling={"api": ()})
    assert unknown.verdict("mcp.example.test").ceiling == "unknown"
    assert unknown.verdict("mcp.example.test").ok
    outside = _policy(rule, ceiling={"api": ("api.github.com",)})
    verdict = outside.verdict("mcp.example.test")
    assert (verdict.org, verdict.ceiling) == ("allowed", "outside")
    assert not verdict.ok and verdict.reason == "ceiling_outside"
    # another family has no declared ceiling: unknown, so not checked
    assert outside.verdict("mcp.example.test", 443, "workers").ceiling == "unknown"
    wild = _policy(rule, ceiling={"api": ("*.example.test",)})
    assert wild.verdict("mcp.example.test").ceiling == "open"


def test_check_modes() -> None:
    enforce = _policy()
    with pytest.raises(EgressRefusedError) as refused:
        enforce.check("evil.example", 443, "api")
    assert refused.value.reason == "org_absent"
    assert "evil.example" in str(refused.value) and "/admin/egress" in str(refused.value)
    assert _policy(mode="off").check("evil.example").org == "absent"
    assert _policy(mode="warn").check("evil.example").org == "absent"


def test_scope_and_fail_closed_without_a_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    assert current_scope() is None
    with pytest.raises(EgressRefusedError) as refused:
        check_current("a.example")
    assert refused.value.reason == "no_policy_in_context"
    with use_policy(_policy(AllowedRule("a.example")), "api", "test"):
        check_current("a.example")
        with pytest.raises(EgressRefusedError):
            check_current("b.example")
    assert current_scope() is None
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "warn")
    check_current("a.example")


def test_default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_EGRESS_MODE", raising=False)
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    assert default_mode() == "enforce"
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert default_mode() == "warn"
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "off")
    assert default_mode() == "off"
    assert default_mode("enforce") == "enforce"


def test_ceiling_from_drops_unknown_families() -> None:
    assert ceiling_from({"api": ["API.GitHub.com", "*.x.test"], "pods": ["a.b"]}) == {
        "api": ("api.github.com", "*.x.test")
    }
