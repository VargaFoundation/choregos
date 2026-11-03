"""The agent, skill, auth, project and work-item routers are English (S45-15, ADR 0098), and not one
route, operation id or persisted audit key moved while they were translated."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "apps/api/src/choregos_api/routers/"
RECORDED = pathlib.Path(__file__).parent / "fixtures" / "routes_before_s45_15.json"

#: The routers S45-15 owns. Not listed: the nine of S45-16 (internal, org_connectors, connectors,
#: connector_catalogue, mcp_catalogue, outbound_proxy, webhooks, runs, actions) and the routers
#: that were written in English (plugins, plugin_sources, mcp_gateway, tracker_identities).
OWNED = (
    "__init__",
    "admin",
    "agent_realtime",
    "agent_releases",
    "agent_session_pods",
    "agent_sessions",
    "agents",
    "auth",
    "budget_alerts",
    "costs",
    "egress",
    "findings",
    "github_app",
    "integrations",
    "knowledge",
    "memories",
    "memory",
    "model_catalogue",
    "models",
    "platform_identity",
    "procedures",
    "projects",
    "readiness",
    "received_events",
    "remote_credentials",
    "skill_catalogue",
    "skills",
    "templates",
    "theme",
    "trains",
    "triggers",
    "ui",
    "workflow_catalogue",
    "workflows",
    "workitems",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_15", ROOT / "tools" / "check_english.py")
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


def test_every_owned_router_exists() -> None:
    present = {path.stem for path in (ROOT / SRC).glob("*.py")}
    assert set(OWNED) <= present, sorted(set(OWNED) - present)


def _served(app: Any) -> set[tuple[str, str, str]]:
    return {
        (method.upper(), path, str(operation.get("operationId", "")))
        for path, item in app.openapi()["paths"].items()
        for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


async def test_routes_unchanged(app: Any) -> None:
    """Every (method, path, operationId) served before the translation is still served."""
    recorded = {(m, p, o) for m, p, o in json.loads(RECORDED.read_text())}
    assert len(recorded) > 300, "the recorded set is the whole API, not a sample"
    missing = recorded - _served(app)
    assert sorted(missing) == []
