"""An installed extension that offers a tool, and a run its tools are called under."""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

EXTENSION = """
from choregos_api.extensions import declare_tool_provider

TOOL = {
    "name": "internal_directory",
    "description": "Looks a person up in the project's directory.",
    "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
}


async def list_tools(session, run, project):
    return [TOOL]


async def call_tool(session, run, project, name, arguments):
    return 200, {"run": run.id, "project": project.slug, "tool": name, "arguments": arguments}


def attach():
    declare_tool_provider("extension_tools", list_tools, call_tool)
"""


@pytest.fixture(name="extension_tools")
def installed_extension(tmp_path: pathlib.Path) -> Iterator[None]:
    """An extension installed as a real module with its `.dist-info`, loaded by `create_app()`."""
    from choregos_api.extensions import reset

    root = tmp_path / "site"
    root.mkdir()
    (root / "extension_tools.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_tools-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: extension_tools\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_tools = extension_tools:attach\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_tools", None)
        reset()


async def run_extension_tool(project_id: str) -> tuple[str, dict[str, str]]:
    """A running run of the project and the headers of its run token."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key="varga/x#11", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-greffon-1",
                work_item_id=item.id,
                project_id=project_id,
                stage_role="custom",
                status="running",
            )
        )
    token = mint_run_token(
        "run-greffon-1", project_slug="billing-api", work_item_key="varga/x#11", ttl_minutes=30
    )
    headers: dict[str, Any] = {"Authorization": f"Bearer {token}"}
    return "run-greffon-1", headers
