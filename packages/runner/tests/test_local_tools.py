"""The runner serves the platform's tools when nobody else does.

With Tekton, a sidecar serves them. With the `k8s_job` executor, the agent pod has ONLY ONE
container: nobody listened on the announced address, and the agent ended up with no
tool at all, while believing it had some, its `StageInput` promising it the address. The worst
of both worlds: it tries, fails silently, and falls back on what it can do alone.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from choregos_runner.local_tools import start_if_absent, stop

pytestmark = pytest.mark.asyncio


class _SilentClient:
    """The server only needs a client: it does not use it at startup."""

    run_id = "run-1"


async def test_a_remote_address_is_not_our_business() -> None:
    """An MCP server hosted elsewhere is joined, not started."""
    assert await start_if_absent("https://mcp.example/mcp", _SilentClient()) is None  # type: ignore[arg-type]


async def test_a_sidecar_already_in_place_keeps_control() -> None:
    """We do not replace what works: if someone is listening, we start nothing."""
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        handle = await start_if_absent(f"http://127.0.0.1:{port}/mcp", _SilentClient())  # type: ignore[arg-type]
        assert handle is None
    finally:
        server.close()
        await server.wait_closed()


async def test_with_nobody_listening_the_runner_serves_the_tools() -> None:
    """And the agent finds `report_finding`, `ask_human` and the catalogue again."""
    import socket

    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    handle = await start_if_absent(f"http://127.0.0.1:{port}/mcp", _SilentClient())  # type: ignore[arg-type]
    assert handle is not None, "nobody was listening: the runner had to take over"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=5,
            )
        names = [tool["name"] for tool in response.json()["result"]["tools"]]
        assert "report_finding" in names, names
    finally:
        await stop(handle)


async def test_stop_accepts_the_absence(_: Any = None) -> None:
    """Nothing to stop is not an error: the sidecar path goes through here."""
    await stop(None)
