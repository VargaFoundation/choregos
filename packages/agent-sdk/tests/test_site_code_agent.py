# SPDX-License-Identifier: Apache-2.0
"""The code agent of the public page `docs/site/build/agents.md` runs under the runner (S62-09b).

The page's Python block is written to a file and started exactly as the runner starts a code agent,
driven by the runner's own ACP client against a gateway and a real MCP server: it must ask the guard
before its tool call, write its deliverable, and leave a valid result. A page whose agent stops
working with the SDK turns this red.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from choregos_runner.acp import AcpClient
from choregos_runner.result import load_result

from .conftest import RUN_KEY, Gateway, McpFake

PAGE = Path(__file__).resolve().parents[3] / "docs" / "site" / "build" / "agents.md"


def _agent_source() -> str:
    blocks = re.findall(r"```python\n(.*?)```", PAGE.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, "the page should show one agent in Python"
    return blocks[0]


async def test_the_page_s_agent_runs_through_the_runner_s_client(
    tmp_path: Path, gateway: Gateway, mcp_server: McpFake
) -> None:
    script = tmp_path / "agent" / "ticket_triager.py"
    script.parent.mkdir()
    script.write_text(_agent_source(), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {
        "OPENAI_BASE_URL": gateway.url,
        "OPENAI_API_KEY": RUN_KEY,
        "OPENAI_MODEL": "platform/standard",
        "CHOREGOS_MAX_TURNS": "10",
    }
    asked: list[str] = []

    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        asked.append(str(params["toolCall"].get("title")))
        return True, "ok"

    client = AcpClient(
        [sys.executable, str(script)], cwd=str(workspace), env=env, on_permission=on_permission
    )
    servers = [{"name": "choregos-tools", "type": "http", "url": mcp_server.url, "headers": []}]
    async with client:
        info = await client.initialize()
        await client.new_session(str(workspace), servers)
        await client.prompt("Customers get a 500 when they download a refunded invoice.", timeout=60)
    assert info["agentInfo"]["name"] == "ticket-triager"
    assert client.outcome.stop_reason == "end_turn", client.stderr_tail
    assert any(event.startswith("mcp:knowledge_search:") for event in mcp_server.events), mcp_server.events
    assert asked, "the runner's guard was never asked"
    assert (workspace / ".choregos" / "outputs" / "triage.md").read_text(encoding="utf-8")
    loaded = load_result(workspace / ".choregos" / "result.json")
    assert loaded.ok, loaded.error
    assert loaded.result is not None and loaded.result.summary == "triaged"
    assert loaded.result.evidence.facts == {"has_context": True}
    assert all(seen["authorization"] == f"Bearer {RUN_KEY}" for seen in gateway.seen)
