# SPDX-License-Identifier: Apache-2.0
"""S47-29: `routers/agents/` is a package of small modules, and its routes did not move.

FastAPI matches routes in registration order, so the ordered (path, methods, operation id) list is
pinned against a fixture captured from the single-file router before it was split.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_api.routers import agents

PACKAGE = Path(agents.__file__).parent
FIXTURE = Path(__file__).parent / "fixtures" / "agents_routes.json"
MAX_LINES = 600


def test_the_routes_keep_their_order_and_ids() -> None:
    current = [
        {"path": route.path, "methods": sorted(route.methods), "operation_id": route.operation_id}  # type: ignore[attr-defined]
        for route in agents.router.routes
    ]
    assert current == json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_no_module_of_the_package_exceeds_600_lines() -> None:
    assert PACKAGE.name == "agents" and (PACKAGE / "__init__.py").is_file()
    too_long = {
        path.name: lines
        for path in sorted(PACKAGE.glob("*.py"))
        if (lines := len(path.read_text(encoding="utf-8").splitlines())) > MAX_LINES
    }
    assert not too_long, f"modules over {MAX_LINES} lines: {too_long}"


def test_the_public_names_still_import() -> None:
    from choregos_api.routers.agents import AgentRef, Slug, router

    assert router is agents.router
    assert Slug is not None and AgentRef is not None
