"""The inbound MCP door is English (S45-14, ADR 0098), and its former names still resolve.

Tool names, scopes, JSON-RPC errors and the persisted audit keys do not change: the door's wire
behaviour is proven by `test_mcp_door.py`, which this story translated without changing a call.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOOR = "apps/api/src/choregos_api/mcp/"
OWNED = (
    f"{DOOR}__init__.py",
    f"{DOOR}caller.py",
    f"{DOOR}limits.py",
    f"{DOOR}oauth.py",
    f"{DOOR}tools.py",
    f"{DOOR}transport.py",
    f"{DOOR}appelant.py",
    f"{DOOR}garde_fous.py",
    f"{DOOR}outils.py",
    "apps/api/tests/test_mcp_door.py",
    "apps/api/tests/test_mcp_oauth.py",
    "apps/api/tests/test_scoped_tokens.py",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_14", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_mcp_door_has_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree(list(OWNED))
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_former_door_modules_still_import() -> None:
    from choregos_api.mcp import caller, limits, oauth, tools, transport
    from choregos_api.rate_limit import RateLimiter

    for former, current in (
        ("appelant", caller),
        ("garde_fous", limits),
        ("outils", tools),
    ):
        assert importlib.import_module(f"choregos_api.mcp.{former}") is current

    from choregos_api.mcp.appelant import Appelant, Refus, identifier, incarner_l_agent
    from choregos_api.mcp.garde_fous import ECRITURES_PAR_JOUR, LIMITEUR, ecritures_du_jour, tronquer
    from choregos_api.mcp.oauth import emetteur
    from choregos_api.mcp.outils import Contexte, Outil, OutilRefuse, annonces
    from choregos_api.mcp.transport import TAILLE_MAX_DU_CORPS, _origines_admises, _refus_http

    assert Appelant is caller.Caller and Refus is caller.Refusal
    assert identifier is caller.identify and incarner_l_agent is caller.act_as_agent
    assert ECRITURES_PAR_JOUR == limits.WRITES_PER_DAY and LIMITEUR is limits.RATE_LIMITER
    assert isinstance(LIMITEUR, RateLimiter)
    assert ecritures_du_jour is limits.writes_today and tronquer is limits.truncate
    assert emetteur is oauth.issuer_of
    assert (Contexte, Outil, OutilRefuse) == (tools.Context, tools.Tool, tools.ToolRefused)
    assert annonces is tools.announced_tools
    assert TAILLE_MAX_DU_CORPS == transport.MAX_BODY_SIZE
    assert _origines_admises is transport._allowed_origins
    assert _refus_http is transport._http_refusal


def test_the_former_caller_names_still_read_and_construct() -> None:
    from choregos_api.mcp.caller import Caller, Refusal
    from choregos_api.rbac import Principal

    principal = Principal(user_id="u", email="u@example.test")
    caller = Caller(principal=principal, token=None, scopes=frozenset({"mcp:write"}), key="oauth:c:s")
    assert (caller.cle, caller.jeton, caller.portees, caller.ecrit) == (
        "oauth:c:s",
        None,
        frozenset({"mcp:write"}),
        True,
    )
    assert caller.writes and caller.key == caller.cle
    # The A2A door and the test of the external agents still build and read it with the French names.
    former = Caller(
        principal=principal, jeton=None, portees=frozenset({"mcp:read"}), motifs=("list_projects",)
    )
    assert former.reasons == former.motifs == ("list_projects",)
    assert not former.writes
    with pytest.raises(TypeError):
        Caller(principal=principal)
    refusal = Refusal(401, "no", erreur="invalid_token")
    assert (refusal.status, refusal.statut, refusal.error, refusal.erreur) == (
        401,
        401,
        "invalid_token",
        "invalid_token",
    )


def test_the_former_http_refusal_keywords_still_work() -> None:
    from choregos_api.mcp.transport import _http_refusal

    response = _http_refusal(401, "no", erreur_oauth="invalid_token", chemin="/mcp/projects/o:p")
    assert response.headers["www-authenticate"] == 'Bearer realm="choregos", error="invalid_token"'
    assert _http_refusal(401, "no", oauth_error="invalid_token").headers == response.headers
