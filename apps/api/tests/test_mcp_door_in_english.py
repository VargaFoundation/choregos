"""The inbound MCP door is English (S45-14, ADR 0098), and its former names still resolve.

Tool names, scopes, JSON-RPC errors and the persisted audit keys do not change: the door's wire
behaviour is proven by `test_mcp_door.py`, which this story translated without changing a call.
"""

from __future__ import annotations

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


def test_the_caller_and_the_refusal_carry_the_english_fields() -> None:
    from choregos_api.mcp.caller import Caller, Refusal
    from choregos_api.rbac import Principal

    principal = Principal(user_id="u", email="u@example.test")
    caller = Caller(principal=principal, token=None, scopes=frozenset({"mcp:write"}), key="oauth:c:s")
    assert (caller.key, caller.token, caller.scopes) == ("oauth:c:s", None, frozenset({"mcp:write"}))
    assert caller.writes
    reading = Caller(
        principal=principal, token=None, scopes=frozenset({"mcp:read"}), reasons=("list_projects",)
    )
    assert reading.reasons == ("list_projects",)
    assert not reading.writes
    refusal = Refusal(401, "no", error="invalid_token")
    assert (refusal.status, refusal.error) == (401, "invalid_token")


def test_the_http_refusal_names_the_oauth_error_and_the_door() -> None:
    from choregos_api.mcp.transport import _http_refusal

    response = _http_refusal(401, "no", oauth_error="invalid_token", path="/mcp/projects/o:p")
    assert response.headers["www-authenticate"] == 'Bearer realm="choregos", error="invalid_token"'
