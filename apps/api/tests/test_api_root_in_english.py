"""The API root modules, the edition extension API and the Principal are English (S45-12, ADR 0098),
and their former French names are gone since 0.24.0 (S45-49)."""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import warnings
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "apps/api/src/choregos_api/"

OWNED = (
    "__init__",
    "adapter_wiring",
    "bootstrap",
    "audit",
    "catalogue",
    "config",
    "deps",
    "edition",
    "errors",
    "events",
    "freshness",
    "rate_limit",
    "local_demo",
    "logging",
    "main",
    "metrics",
    "object_store",
    "rbac",
    "secret_store",
    "security",
    "temporal",
    "theme",
    "trace_drift",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_12", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_api_root_has_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree([f"{SRC}{name}.py" for name in OWNED])
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_the_rate_limiter_reads_its_window_from_the_module(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api import rate_limit

    monkeypatch.setattr(rate_limit, "WINDOW_S", 1.0)
    limiter = rate_limit.RateLimiter(per_minute=1)
    assert limiter.admits("k", 0.0) == (True, 0)
    assert limiter.admits("k", 0.5) == (False, 1)


#: The edition's French names, removed in 0.24.0 (S45-49).
FORMER_EDITION_NAMES = (
    "COMMUNAUTAIRE",  # lang: fr-ok - removed, asserted gone
    "ENTREPRISE",  # lang: fr-ok - removed, asserted gone
    "declarer",  # lang: fr-ok - removed, asserted gone
    "declarer_le_mappeur_de_groupes",  # lang: fr-ok - removed, asserted gone
    "mappeur_de_groupes",  # lang: fr-ok - removed, asserted gone
    "courante",  # lang: fr-ok - removed, asserted gone
    "fonctions",  # lang: fr-ok - removed, asserted gone
    "est_entreprise",  # lang: fr-ok - removed, asserted gone
    "SessionRefusee",  # lang: fr-ok - removed, asserted gone
    "declarer_une_validation_de_session",  # lang: fr-ok - removed, asserted gone
    "validations_de_session",  # lang: fr-ok - removed, asserted gone
    "declarer_un_administrateur_de_plateforme",  # lang: fr-ok - removed, asserted gone
    "administrateurs_de_plateforme",  # lang: fr-ok - removed, asserted gone
    "reinitialiser",  # lang: fr-ok - removed, asserted gone
)


@pytest.mark.parametrize("former", FORMER_EDITION_NAMES)
def test_edition_former_names_are_gone(former: str) -> None:
    from choregos_api import edition

    assert not hasattr(edition, former)


def test_edition_declare_refuses_the_former_keyword() -> None:
    from choregos_api import edition

    edition.reset()
    try:
        with pytest.raises(TypeError):
            edition.declare(edition.ENTERPRISE, **{"fonctions": ["saml"]})  # lang: fr-ok - asserted gone
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            edition.declare(edition.ENTERPRISE, features=frozenset({"multi-org"}))
        assert edition.features() == frozenset({"multi-org"})
        assert edition.is_enterprise()
    finally:
        edition.reset()
    assert edition.current() == edition.COMMUNITY


def test_principal_former_attribute_is_gone() -> None:
    from choregos_api.rbac import Principal

    principal = Principal(user_id="u", email="u@example.test")
    assert principal.authenticated_at is None
    assert not hasattr(principal, "authentifie_le")  # lang: fr-ok - removed, asserted gone
