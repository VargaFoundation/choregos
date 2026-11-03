"""The internal, connector, webhook, run and action routers are English (S45-16, ADR 0098), and a
patch on the former name of the extension tools helper is still seen by the tools routes."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest
from httpx import AsyncClient

from .test_network_policy_api import _run

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "apps/api/src/choregos_api/routers/"

OWNED = (
    "internal",
    "org_connectors",
    "connectors",
    "connector_catalogue",
    "mcp_catalogue",
    "outbound_proxy",
    "webhooks",
    "runs",
    "actions",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_16", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_owned_routers_have_no_french() -> None:
    checker = _checker()
    try:
        found = checker.scan_tree([f"{SRC}{name}.py" for name in OWNED])
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_the_former_extension_tools_name_is_an_alias() -> None:
    from choregos_api.routers import internal

    assert internal._outils_des_greffons is internal._extension_tools  # type: ignore[attr-defined]


async def test_patching_the_former_extension_tools_name_still_works(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.routers import internal

    headers = await _run(project, "run-former-name-1")

    async def provided(*_args: Any) -> dict[str, Any]:
        return {
            "from_extension": (
                "an-extension",
                {"name": "from_extension", "description": "", "inputSchema": {}},
            )
        }

    monkeypatch.setattr(internal, "_outils_des_greffons", provided)
    listed = await client.get("/api/v1/internal/runs/run-former-name-1/tools", headers=headers)
    assert listed.status_code == 200, listed.text
    assert "from_extension" in {tool["name"] for tool in listed.json()["tools"]}
