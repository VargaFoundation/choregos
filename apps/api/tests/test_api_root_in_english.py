"""The API root modules, the edition extension API and the Principal are English (S45-12, ADR 0098),
and every former name another module imports still resolves to the English object."""

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


def test_edition_former_names_warn_and_resolve() -> None:
    from choregos_api import edition

    edition.reset()
    try:
        with pytest.warns(DeprecationWarning, match="courante is deprecated: use current"):
            assert edition.courante is edition.current
        with pytest.warns(DeprecationWarning, match="declarer is deprecated: use declare"):
            assert edition.declarer is edition.declare
        with pytest.warns(DeprecationWarning, match="fonctions=...") as caught:
            edition.declare(edition.ENTERPRISE, fonctions=["multi-org", "saml"])
        assert "0.23.0" in str(caught[0].message)
        assert edition.features() == frozenset({"multi-org", "saml"})
        assert edition.is_enterprise()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            edition.declare(edition.ENTERPRISE, features=frozenset({"multi-org"}))
        assert edition.features() == frozenset({"multi-org"})
        with pytest.raises(TypeError), pytest.warns(DeprecationWarning):
            edition.declare(edition.ENTERPRISE, features=["a"], fonctions=["b"])
    finally:
        edition.reset()
    assert edition.current() == edition.COMMUNITY


def test_principal_former_attribute_reads_and_writes() -> None:
    from choregos_api.rbac import Principal

    principal = Principal(user_id="u", email="u@example.test")
    assert principal.authenticated_at is None
    principal.authentifie_le = 5
    assert principal.authenticated_at == 5
    assert principal.authentifie_le == 5
    principal.authenticated_at = 7
    assert principal.authentifie_le == 7
