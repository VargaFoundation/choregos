"""A run does not write the gateway's lines of its journal (S65-08b, ADR 0173).

`context.breakdown` has two authors: the run records the context it measured, and the gateway's
callback adds its own count of an ACP CLI's calls (`source: gateway`). The run's door wrote both shapes,
so the console could show a run's number as the gateway's. Now the door refuses a breakdown that says
the gateway measured it; the run's own breakdowns pass. Numbers above `GATEWAY_SEQ_BASE` are not the
gateway's alone: the run's tool bridge numbers its lines there (`SIDECAR_SEQ_BASE`), and they pass.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_tools_mcp.bridge import SIDECAR_SEQ_BASE
from httpx import AsyncClient

from .kits.closed_runs import token_of
from .kits.context_breakdown import gateway_breakdown, run_with_events, set_master_key, signed
from .kits.context_breakdown import journal as _events

RUNS = "/api/v1/internal/runs"


@pytest.fixture(autouse=True)
def master_key(monkeypatch: pytest.MonkeyPatch, app: Any) -> None:
    set_master_key(monkeypatch)


def _breakdown(seq: int, source: str) -> dict[str, Any]:
    return {"seq": seq, "type": "context.breakdown", "payload": gateway_breakdown(seq, 900, source=source)}


async def _post(client: AsyncClient, run_id: str, *events: dict[str, Any]) -> Any:
    return await client.post(
        f"{RUNS}/{run_id}/events", headers=token_of(run_id), json={"events": list(events)}
    )


async def test_a_run_cannot_say_the_gateway_measured_its_breakdown(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await run_with_events(project, "run-gl-1", "claude-code", [])

    refused = await _post(client, "run-gl-1", _breakdown(1, "gateway"))

    assert refused.status_code == 422, refused.text
    assert "gateway" in refused.json()["detail"]
    assert await _events("run-gl-1") == []


async def test_a_batch_that_carries_one_is_refused_whole(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await run_with_events(project, "run-gl-3", "claude-code", [])

    own = {"seq": 1, "type": "session/update", "payload": {"text": "implementing"}}
    refused = await _post(client, "run-gl-3", own, _breakdown(2, "gateway"))

    assert refused.status_code == 422, refused.text
    assert await _events("run-gl-3") == []


@pytest.mark.parametrize("source", ["loop", "hosted_proxy"])
async def test_the_runs_own_breakdowns_are_still_written(
    client: AsyncClient, project: dict[str, Any], source: str
) -> None:
    await run_with_events(project, f"run-gl-4-{source}", "prompt", [])

    posted = await _post(client, f"run-gl-4-{source}", _breakdown(1, source))

    assert posted.status_code == 202, posted.text
    [(seq, type_, payload)] = await _events(f"run-gl-4-{source}")
    assert (seq, type_, payload["source"]) == (1, "context.breakdown", source)


async def test_the_tool_bridges_lines_still_pass_and_the_gateway_writes_after_them(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The tool bridge numbers its lines from `SIDECAR_SEQ_BASE`, the gateway's base too: the door lets
    them through, and the gateway's next breakdown takes the number after the highest it finds."""
    await run_with_events(project, "run-gl-5", "claude-code", [])
    bridge = SIDECAR_SEQ_BASE + 1_234
    posted = await _post(client, "run-gl-5", {"seq": bridge, "type": "tools.client", "payload": {}})
    assert posted.status_code == 202, posted.text

    kept = await client.post(
        f"{RUNS}/run-gl-5/context-breakdown", headers=signed("run-gl-5"), json=gateway_breakdown(1, 1200)
    )

    assert kept.status_code == 202, kept.text
    lines = await _events("run-gl-5")
    assert [(seq, type_) for seq, type_, _ in lines] == [
        (bridge, "tools.client"),
        (bridge + 1, "context.breakdown"),
    ]
    assert lines[1][2]["source"] == "gateway" and lines[1][2]["prompt_tokens"] == 1200
