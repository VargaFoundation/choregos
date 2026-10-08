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
    monkeypatch.setenv("JIRA_TOKEN", "s3cr3t")
    assert secrets.resoudre("env:JIRA_TOKEN") == "s3cr3t"


def test_une_variable_absente_est_nommee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    with pytest.raises(SecretIntrouvable, match="env:JIRA_TOKEN"):
        secrets.resoudre("env:JIRA_TOKEN")


@pytest.mark.parametrize("valeur", ["s3cr3t", "ghp_abcdef0123456789", "inconnu:x", "env:minuscules"])
def test_une_valeur_en_clair_ou_illisible_est_refusee(valeur: str) -> None:
    with pytest.raises(ReferenceInvalide):
        secrets.resoudre(valeur)


def test_un_greffon_declare_son_coffre() -> None:
    secrets.declarer_un_resolveur("vault", lambda chemin: f"lu:{chemin}")
    assert secrets.resoudre("vault:kv/jira#token") == "lu:kv/jira#token"
    assert "vault" in secrets.schemas()
    with pytest.raises(ValueError, match="already declared"):
        secrets.declarer_un_resolveur("vault", lambda chemin: chemin)
