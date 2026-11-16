"""A run does not write the gateway's lines of its journal (S65-08b, ADR 0173).

`context.breakdown` has two authors: the run records the context it measured, and the gateway's
callback adds its own count of an ACP CLI's calls (`source: gateway`, numbered above
`GATEWAY_SEQ_BASE`). The run's door wrote both shapes, so the console could show a run's number as the
gateway's, and a run could take the number the gateway writes next. Now the door refuses a line in the
gateway's range and a breakdown that says the gateway measured it; the run's own breakdowns pass.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api.services.context_breakdown import GATEWAY_SEQ_BASE
from httpx import AsyncClient

from .kits.closed_runs import token_of
from .kits.context_breakdown import run_with_events
from .test_gateway_context_breakdown import (
    _events,
    gateway_breakdown,
    master_key,  # noqa: F401 (the callback signs with it)
    signed,
)

RUNS = "/api/v1/internal/runs"


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


@pytest.mark.parametrize("type_", ["context.breakdown", "session/update"])
async def test_a_run_cannot_number_a_line_in_the_gateways_range(
    client: AsyncClient, project: dict[str, Any], type_: str
) -> None:
    await run_with_events(project, "run-gl-2", "claude-code", [])

    refused = await _post(
        client, "run-gl-2", {"seq": GATEWAY_SEQ_BASE + 1, "type": type_, "payload": {"source": "loop"}}
    )

    assert refused.status_code == 422, refused.text
    assert await _events("run-gl-2") == []


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


async def test_the_gateways_next_line_stays_the_gateways(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The gateway numbers its next line after its last one: a run that took that number first would
    make the gateway's write collide with it. Refused at the door, it is the gateway's."""
    await run_with_events(project, "run-gl-5", "claude-code", [])
    taken = await _post(
        client, "run-gl-5", {"seq": GATEWAY_SEQ_BASE + 1, "type": "session/update", "payload": {}}
    )
    assert taken.status_code == 422, taken.text

    kept = await client.post(
        f"{RUNS}/run-gl-5/context-breakdown", headers=signed("run-gl-5"), json=gateway_breakdown(1, 1200)
    )

    assert kept.status_code == 202, kept.text
    [(seq, type_, payload)] = await _events("run-gl-5")
    assert (seq, type_, payload["source"], payload["prompt_tokens"]) == (
        GATEWAY_SEQ_BASE + 1,
        "context.breakdown",
        "gateway",
        1200,
    )
