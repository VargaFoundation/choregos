"""Secret references (ADR 0034, S19-01): `env:` in the core, others through an extension."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from choregos_core import secrets
from choregos_core.secrets import InvalidReference, SecretNotFound


@pytest.fixture(autouse=True)
def clean_resolvers() -> Iterator[None]:
    secrets.reset()
    yield
    secrets.reset()


def test_an_environment_reference_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_JIRA_TOKEN", "s3cr3t")
    assert secrets.resolve("env:CONNECTOR_JIRA_TOKEN") == "s3cr3t"


def test_a_missing_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECTOR_JIRA_TOKEN", raising=False)
    with pytest.raises(SecretNotFound, match="env:CONNECTOR_JIRA_TOKEN"):
        secrets.resolve("env:CONNECTOR_JIRA_TOKEN")


@pytest.mark.parametrize("value", ["s3cr3t", "ghp_abcdef0123456789", "unknown:x", "env:lowercase"])
def test_a_clear_or_unreadable_value_is_refused(value: str) -> None:
    with pytest.raises(InvalidReference):
        secrets.resolve(value)


def test_env_reference_reads_only_connector_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """S39-06b: a reference is written by a tenant role; the platform's own secrets stay out of reach."""
    monkeypatch.delenv("CHOREGOS_SECRET_ENV_PATTERN", raising=False)
    for platform in (
        "CHOREGOS_SESSION_SECRET",
        "CHOREGOS_GATEWAY_MASTER_KEY",
        "DATABASE_URL",
        "PGPASSWORD",
        "HOME",
    ):
        monkeypatch.setenv(platform, "platform-secret")
        with pytest.raises(InvalidReference, match="only variables matching"):
            secrets.check(f"env:{platform}")  # refused at write
        with pytest.raises(SecretNotFound, match="only variables matching"):
            secrets.resolve(f"env:{platform}")  # and at resolution, even though it is set
    monkeypatch.setenv("CONNECTOR_JIRA", "ok")
    secrets.check("env:CONNECTOR_JIRA")
    assert secrets.resolve("env:CONNECTOR_JIRA") == "ok"
    # A deployment widens the pattern: its own variables, never the platform's.
    monkeypatch.setenv("CHOREGOS_SECRET_ENV_PATTERN", r"^(CONNECTOR_|JIRA_)[A-Z0-9_]*$")
    monkeypatch.setenv("JIRA_TOKEN", "t0k")
    assert secrets.resolve("env:JIRA_TOKEN") == "t0k"
    monkeypatch.setenv("CHOREGOS_SECRET_ENV_PATTERN", r"^.*$")
    with pytest.raises(InvalidReference):
        secrets.check("env:CHOREGOS_SESSION_SECRET")


def test_an_extension_declares_its_vault() -> None:
    secrets.declare_resolver("vault", lambda path: f"read:{path}")
    assert secrets.resolve("vault:kv/jira#token") == "read:kv/jira#token"
    assert "vault" in secrets.schemas()
    with pytest.raises(ValueError, match="already declared"):
        secrets.declare_resolver("vault", lambda path: path)


def test_a_store_reference_is_known_but_never_resolved_by_the_core() -> None:
    """`store:` values are sealed in the database (ADR 0058): the sync core accepts the scheme at
    write time, and a call site that forgot the platform's resolver fails loudly, not silently."""
    assert "store" in secrets.schemas()
    secrets.check("store:connector/jira/api_token")
    assert secrets.is_store_reference("store:connector/jira/api_token")
    assert not secrets.is_store_reference("env:X") and not secrets.is_store_reference(None)
    with pytest.raises(SecretNotFound, match="resolved by the platform"):
        secrets.resolve("store:connector/jira/api_token")
