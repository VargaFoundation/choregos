"""The gateway's callback and the API (ADR 0141 §4, S64-08b): an ACP CLI's context breakdowns.

The callback reads what the runner registered (`context.parts`) and posts each breakdown it keeps,
signed for ONE run with the gateway's master key: a run token, another run's signature or no master key
at all is refused. A posted breakdown is written as the run's `context.breakdown` event, numbers only,
`source: gateway`, with a `seq` above every runner's (the runner keeps writing its own). An ACP CLI's
run is then measurable; before the first breakdown, it says so and serves no `latest`. A run the
platform measures itself takes nothing from the gateway.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_core import llm_spans
from choregos_core.context_breakdown import register
from httpx import AsyncClient
from sqlalchemy import select

from .test_context_breakdown import _run

MASTER_KEY = "-".join(["sk", "master", "c0ffee" * 3])
CANARY = "canary-3d1e-what-the-ticket-said"


@pytest.fixture(autouse=True)
def master_key(monkeypatch: pytest.MonkeyPatch, app: Any) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_GATEWAY_MASTER_KEY", MASTER_KEY)
    reset_settings_cache()


def signed(run_id: str, key: str = MASTER_KEY) -> dict[str, str]:
    """The callback's own header, from the callback's own function."""
    return {"authorization": f"Bearer {llm_spans.context_signature(key, run_id)}"}


def gateway_breakdown(call: int, tokens: int, **extra: Any) -> dict[str, Any]:
    return {
        "call": call,
        "at": f"2026-11-11T10:00:{call:02d}+00:00",
        "model": "claude-sonnet",
        "prompt_tokens": tokens,
        "max_input_tokens": None,
        "source": "gateway",
        "sections": {"system": tokens // 10, "skills": tokens // 10, "backend": tokens - 2 * (tokens // 10)},
        **extra,
    }


async def _events(run_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(RunEvent.seq, RunEvent.type, RunEvent.payload)
            .where(RunEvent.run_id == run_id)
            .order_by(RunEvent.seq)
        )
        return [(seq, type_, dict(payload)) for seq, type_, payload in rows.all()]


PARTS = {
    "parts": register({"system": ["You implement one step of the workflow, then write the result file."]}),
    "mcp_servers": ["crm"],
}


async def test_the_callback_reads_what_the_runner_registered(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(project, "run-gw-1", "claude-code", [("session/new", {}), ("context.parts", PARTS)])

    answer = await client.get("/api/v1/internal/runs/run-gw-1/context-parts", headers=signed("run-gw-1"))

    assert answer.status_code == 200, answer.text
    assert answer.json() == PARTS
    await _run(project, "run-gw-2", "claude-code", [])
    empty = await client.get("/api/v1/internal/runs/run-gw-2/context-parts", headers=signed("run-gw-2"))
    assert empty.json() == {"parts": [], "mcp_servers": []}, "nothing registered yet: all `backend`"


async def test_a_kept_breakdown_makes_an_acp_clis_run_measurable(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(project, "run-gw-3", "claude-code", [("session/new", {})])
    before = (await client.get("/api/v1/runs/run-gw-3/context-breakdown")).json()
    assert before["measurable"] is False and before["latest"] is None

    for call, tokens in [(1, 12_000), (4, 30_000)]:
        posted = await client.post(
            "/api/v1/internal/runs/run-gw-3/context-breakdown",
            json=gateway_breakdown(call, tokens),
            headers=signed("run-gw-3"),
        )
        assert posted.status_code == 202, posted.text

    after = (await client.get("/api/v1/runs/run-gw-3/context-breakdown")).json()
    assert after["measurable"] is True and after["backend"] == "claude-code"
    assert [(p["call"], p["prompt_tokens"]) for p in after["series"]] == [(1, 12_000), (4, 30_000)]
    assert after["latest"]["source"] == "gateway"
    assert sum(after["latest"]["sections"].values()) == 30_000


async def test_the_gateways_events_never_take_a_runners_seq(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The gateway writes while the runner does: a breakdown posted first must not make the runner's
    next events look like replays, nor the other way round."""
    from choregos_api.security import mint_run_token

    await _run(project, "run-gw-4", "claude-code", [("session/new", {})])
    await client.post(
        "/api/v1/internal/runs/run-gw-4/context-breakdown",
        json=gateway_breakdown(1, 900),
        headers=signed("run-gw-4"),
    )
    token = mint_run_token("run-gw-4", project_slug="x", work_item_key="x", ttl_minutes=5)
    runner = await client.post(
        "/api/v1/internal/runs/run-gw-4/events",
        json={"events": [{"seq": 2, "type": "session/update", "payload": {}}]},
        headers={"authorization": f"Bearer {token}"},
    )
    assert runner.json() == {"accepted": 1}, "the runner's seq 2 is not taken"
    await client.post(
        "/api/v1/internal/runs/run-gw-4/context-breakdown",
        json=gateway_breakdown(2, 950),
        headers=signed("run-gw-4"),
    )

    events = await _events("run-gw-4")
    assert [(seq, type_) for seq, type_, _ in events] == [
        (1, "session/new"),
        (2, "session/update"),
        (1_000_000_001, "context.breakdown"),
        (1_000_000_002, "context.breakdown"),
    ]


async def test_only_numbers_are_written_and_the_source_is_the_gateways(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(project, "run-gw-5", "codex", [])
    posted = await client.post(
        "/api/v1/internal/runs/run-gw-5/context-breakdown",
        json=gateway_breakdown(
            1,
            500,
            source="loop",
            text=CANARY,
            sections={"system": 100, "backend": 400, "the ticket": 7, CANARY: 1},
        ),
        headers=signed("run-gw-5"),
    )

    assert posted.status_code == 202, posted.text
    ((_, _, written),) = await _events("run-gw-5")
    assert written["source"] == "gateway"
    assert CANARY not in json.dumps(written) and "the ticket" not in json.dumps(written)
    assert set(written) == {"call", "at", "model", "prompt_tokens", "max_input_tokens", "source", "sections"}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="none"),
        pytest.param(signed("another-run"), id="another-runs-signature"),
        pytest.param(signed("run-gw-6", key="sk-not-the-master-key"), id="another-key"),
        pytest.param({"authorization": f"Bearer {MASTER_KEY}"}, id="the-master-key-itself"),
        pytest.param("run-token", id="the-runs-own-token"),
    ],
)
async def test_only_the_gateways_signature_for_this_run_is_accepted(
    client: AsyncClient, project: dict[str, Any], headers: Any
) -> None:
    from choregos_api.security import mint_run_token

    if headers == "run-token":
        token = mint_run_token("run-gw-6", project_slug="x", work_item_key="x", ttl_minutes=5)
        headers = {"authorization": f"Bearer {token}"}
    await _run(project, "run-gw-6", "claude-code", [("context.parts", PARTS)])

    read = await client.get("/api/v1/internal/runs/run-gw-6/context-parts", headers=headers)
    write = await client.post(
        "/api/v1/internal/runs/run-gw-6/context-breakdown", json=gateway_breakdown(1, 10), headers=headers
    )

    assert read.status_code in {401, 403}, read.text
    assert write.status_code in {401, 403}, write.text
    assert [type_ for _, type_, _ in await _events("run-gw-6")] == ["context.parts"]


async def test_without_a_master_key_the_routes_open_to_nobody(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_GATEWAY_MASTER_KEY", "")
    reset_settings_cache()
    await _run(project, "run-gw-7", "claude-code", [])

    answer = await client.get(
        "/api/v1/internal/runs/run-gw-7/context-parts", headers=signed("run-gw-7", key="")
    )

    assert answer.status_code == 403, answer.text


@pytest.mark.parametrize("backend", ["prompt", "hosted"])
async def test_a_run_the_platform_measures_itself_takes_nothing_from_the_gateway(
    client: AsyncClient, project: dict[str, Any], backend: str
) -> None:
    run_id = f"run-gw-{backend}"
    await _run(project, run_id, backend, [])

    posted = await client.post(
        f"/api/v1/internal/runs/{run_id}/context-breakdown",
        json=gateway_breakdown(1, 10),
        headers=signed(run_id),
    )

    assert posted.status_code == 409, posted.text
    assert await _events(run_id) == []


async def test_what_is_not_a_breakdown_is_refused(client: AsyncClient, project: dict[str, Any]) -> None:
    await _run(project, "run-gw-8", "claude-code", [])

    posted = await client.post(
        "/api/v1/internal/runs/run-gw-8/context-breakdown",
        json={"call": "first", "text": CANARY},
        headers=signed("run-gw-8"),
    )
    missing = await client.post(
        "/api/v1/internal/runs/missing/context-breakdown",
        json=gateway_breakdown(1, 10),
        headers=signed("missing"),
    )

    assert posted.status_code == 422, posted.text
    assert missing.status_code == 404, missing.text
    assert await _events("run-gw-8") == []
