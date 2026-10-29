"""The account a connector writes under, as the console shows it (S42-06g, ADR 0107 point 4)."""

from __future__ import annotations

import pytest
from choregos_adapters import account_of


@pytest.mark.parametrize(
    ("kind", "type_name", "config", "account"),
    [
        (
            "tracker",
            "jira",
            {"email": "bot@acme.example", "api_token": "env:CONNECTOR_JIRA"},
            "bot@acme.example",
        ),
        ("mcp", "confluence", {"email": "wiki@acme.example"}, "wiki@acme.example"),
        (
            "mcp",
            "atlassian-rovo",
            {"auth_scheme": "basic", "username": "vincent@acme.example"},
            "vincent@acme.example",
        ),
        ("tracker", "plugin", {"plugin": "acme-tracker", "server": "issues"}, "acme-tracker/issues"),
        # Nothing in the configuration names the account: the console names the credential instead.
        ("tracker", "github-issues", {"repo": "acme/app"}, None),
        (
            "tracker",
            "gitlab-issues",
            {"base_url": "https://gitlab.example", "token": "env:CONNECTOR_GL"},
            None,
        ),
        ("mcp", "atlassian-rovo", {"auth_scheme": "bearer", "username": "ignored"}, None),
        ("tracker", "jira", {"email": ""}, None),
        ("tracker", "jira", None, None),
    ],
)
def test_the_account_comes_from_the_configuration(
    kind: str, type_name: str, config: dict[str, str] | None, account: str | None
) -> None:
    assert account_of(kind, type_name, config) == account


def test_a_secret_is_never_the_account() -> None:
    config = {"auth_scheme": "basic", "username": "", "password": "s3cret", "token": "s3cret"}
    assert account_of("mcp", "custom", config) is None
