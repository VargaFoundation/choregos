"""The CLI reads C1's neutral names first, and the former ones the API serves until 0.30.0 (ADR 0181)."""

from __future__ import annotations

from tranibor_cli.neutral_names import change_request_of, neutral_role

PR = "https://github.com/northwind/billing-api/pull/7"


def test_the_change_request_is_the_link_first_then_the_former_field() -> None:
    assert change_request_of({"links": [{"rel": "change_request", "url": PR}], "pr_url": None}) == PR
    assert change_request_of({"links": [{"rel": "other", "url": "x"}], "pr_url": PR}) == PR
    assert change_request_of({"pr_url": PR}) == PR
    assert change_request_of({"links": [], "pr_url": None}) is None


def test_a_role_reads_under_its_neutral_name() -> None:
    assert neutral_role("developer") == "contributor"
    assert neutral_role("release_captain") == "approver"
    assert neutral_role("contributor") == "contributor"
    assert neutral_role("org_admin") == "org_admin"
