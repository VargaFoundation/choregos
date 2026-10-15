"""Les références de secret (ADR 0034, S19-01) : `env:` dans le cœur, d'autres par greffon."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from choregos_core import secrets
from choregos_core.secrets import ReferenceInvalide, SecretIntrouvable


@pytest.fixture(autouse=True)
def resolveurs_propres() -> Iterator[None]:
    secrets.reinitialiser()
    yield
    secrets.reinitialiser()


def test_une_reference_d_environnement_se_resout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_JIRA_TOKEN", "s3cr3t")
    assert secrets.resoudre("env:CONNECTOR_JIRA_TOKEN") == "s3cr3t"


def test_une_variable_absente_est_nommee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONNECTOR_JIRA_TOKEN", raising=False)
    with pytest.raises(SecretIntrouvable, match="env:CONNECTOR_JIRA_TOKEN"):
        secrets.resoudre("env:CONNECTOR_JIRA_TOKEN")


@pytest.mark.parametrize("valeur", ["s3cr3t", "ghp_abcdef0123456789", "inconnu:x", "env:minuscules"])
def test_une_valeur_en_clair_ou_illisible_est_refusee(valeur: str) -> None:
    with pytest.raises(ReferenceInvalide):
        secrets.resoudre(valeur)


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
        with pytest.raises(ReferenceInvalide, match="only variables matching"):
            secrets.verifier(f"env:{platform}")  # refused at write
        with pytest.raises(SecretIntrouvable, match="only variables matching"):
            secrets.resoudre(f"env:{platform}")  # and at resolution, even though it is set
    monkeypatch.setenv("CONNECTOR_JIRA", "ok")
    secrets.verifier("env:CONNECTOR_JIRA")
    assert secrets.resoudre("env:CONNECTOR_JIRA") == "ok"
    # A deployment widens the pattern: its own variables, never the platform's.
    monkeypatch.setenv("CHOREGOS_SECRET_ENV_PATTERN", r"^(CONNECTOR_|JIRA_)[A-Z0-9_]*$")
    monkeypatch.setenv("JIRA_TOKEN", "t0k")
    assert secrets.resoudre("env:JIRA_TOKEN") == "t0k"
    monkeypatch.setenv("CHOREGOS_SECRET_ENV_PATTERN", r"^.*$")
    with pytest.raises(ReferenceInvalide):
        secrets.verifier("env:CHOREGOS_SESSION_SECRET")


def test_un_greffon_declare_son_coffre() -> None:
    secrets.declarer_un_resolveur("vault", lambda chemin: f"lu:{chemin}")
    assert secrets.resoudre("vault:kv/jira#token") == "lu:kv/jira#token"
    assert "vault" in secrets.schemas()
    with pytest.raises(ValueError, match="already declared"):
        secrets.declarer_un_resolveur("vault", lambda chemin: chemin)


def test_a_store_reference_is_known_but_never_resolved_by_the_core() -> None:
    """`store:` values are sealed in the database (ADR 0058): the sync core accepts the scheme at
    write time, and a call site that forgot the platform's resolver fails loudly, not silently."""
    assert "store" in secrets.schemas()
    secrets.verifier("store:connector/jira/api_token")
    assert secrets.est_une_reference_du_magasin("store:connector/jira/api_token")
    assert not secrets.est_une_reference_du_magasin("env:X") and not secrets.est_une_reference_du_magasin(
        None
    )
    with pytest.raises(SecretIntrouvable, match="resolved by the platform"):
        secrets.resoudre("store:connector/jira/api_token")
