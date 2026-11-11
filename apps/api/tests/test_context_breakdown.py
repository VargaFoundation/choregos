"""What fills a run's context, read at `GET /runs/{id}/context-breakdown` (ADR 0141 §3-6), and the
session's own breakdown, measured per call in `agent_sessions.respond`.

The route reads the run's `context.breakdown` events: the last one and the series of totals. A
breakdown without the model's window gets it from the organisation's catalogue, and stays `null`
(free space unknown) when the catalogue does not know it. `code` and `remote` runs are not measurable,
nor an ACP CLI's yet. The run's read right is the route's: a person outside the organisation gets 403.
A session's calls are journaled with numbers only, numbered across the session.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .test_agent_sessions import Upstream, _agent, _ask, upstream  # noqa: F401 - the fixture

CANARY = "canary-58c2-what-the-person-typed"


def _breakdown(
    call: int, tokens: int, *, model: str = "platform/standard", window: int | None = None
) -> dict[str, Any]:
    return {
        "call": call,
        "at": f"2026-11-11T10:00:{call:02d}+00:00",
        "model": model,
        "prompt_tokens": tokens,
        "max_input_tokens": window,
        "source": "loop",
        "sections": {"system": tokens // 4, "tools.mcp": tokens // 4, "messages": tokens - 2 * (tokens // 4)},
    }


async def _run(
    project: dict[str, Any], run_id: str, backend: str | None, events: list[tuple[str, dict[str, Any]]]
) -> None:
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"varga/x#{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="triage",
                status="running",
                backend=backend,
            )
        )
        for seq, (type_, payload) in enumerate(events, start=1):
            session.add(RunEvent(run_id=run_id, seq=seq, type=type_, payload=payload, ts=utcnow()))


async def _window(model: str, tokens: int | None) -> None:
    from choregos_api.db.models import ModelCatalogueRow, Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.add(
            ModelCatalogueRow(org_id=org.id, model_name=model, gateway_facts={"max_input_tokens": tokens})
        )


async def test_the_last_breakdown_and_the_series_are_read_from_the_runs_events(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(
        project,
        "run-ctx-1",
        "prompt",
        [
            ("context.breakdown", _breakdown(1, 1000)),
            ("prompt.turn", {"turn": 1, "text": "hello"}),
            ("context.breakdown", _breakdown(4, 5200)),
            ("context.breakdown", {"call": "not a number", "sections": {}}),
            ("context.breakdown", {**_breakdown(9, 8000), "sections": {"messages": 8000, "secret": 1}}),
        ],
    )
    await _window("platform/standard", 200_000)

    answer = await client.get("/api/v1/runs/run-ctx-1/context-breakdown")

    assert answer.status_code == 200, answer.text
    context = answer.json()
    assert context["measurable"] is True and context["backend"] == "prompt"
    assert [(p["call"], p["prompt_tokens"]) for p in context["series"]] == [(1, 1000), (4, 5200), (9, 8000)]
    latest = context["latest"]
    assert latest["call"] == 9 and latest["prompt_tokens"] == 8000
    assert latest["max_input_tokens"] == 200_000, "filled from the catalogue"
    assert "secret" not in latest["sections"], "a section the contract does not name is never served"
    assert sum(latest["sections"].values()) == 8000


async def test_a_model_the_catalogue_does_not_know_leaves_the_free_space_unknown(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(
        project, "run-ctx-2", "hosted", [("context.breakdown", _breakdown(1, 300, model="acme/unknown"))]
    )

    context = (await client.get("/api/v1/runs/run-ctx-2/context-breakdown")).json()

    assert context["latest"]["max_input_tokens"] is None


async def test_a_window_the_runner_knew_is_kept(client: AsyncClient, project: dict[str, Any]) -> None:
    await _run(project, "run-ctx-3", "prompt", [("context.breakdown", _breakdown(1, 300, window=32_000))])
    await _window("platform/standard", 200_000)

    context = (await client.get("/api/v1/runs/run-ctx-3/context-breakdown")).json()

    assert context["latest"]["max_input_tokens"] == 32_000


@pytest.mark.parametrize("backend", ["code", "remote", "claude-code"])
async def test_a_backend_the_platform_cannot_see_into_is_not_measurable(
    client: AsyncClient, project: dict[str, Any], backend: str
) -> None:
    await _run(project, f"run-ctx-{backend}", backend, [])

    context = (await client.get(f"/api/v1/runs/run-ctx-{backend}/context-breakdown")).json()

    assert context == {
        "run_id": f"run-ctx-{backend}",
        "measurable": False,
        "backend": backend,
        "latest": None,
        "series": [],
    }


async def test_the_breakdown_is_read_with_the_runs_read_right(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A run of an organisation the caller has no role in: the route answers what `getRun` answers."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        elsewhere = Project(
            org_id=other.id, slug="shop", name="Shop", status="active", config={"slug": "shop", "org": "acme"}
        )
        session.add(elsewhere)
        await session.flush()
        acme = {"id": elsewhere.id}
    await _run(acme, "run-ctx-4", "prompt", [("context.breakdown", _breakdown(1, 300))])
    await _run(project, "run-ctx-5", "prompt", [("context.breakdown", _breakdown(1, 300))])

    assert (await client.get("/api/v1/runs/run-ctx-5/context-breakdown")).status_code == 200
    assert (await client.get("/api/v1/runs/run-ctx-4")).status_code == 403
    refused = await client.get("/api/v1/runs/run-ctx-4/context-breakdown")
    assert refused.status_code == 403, refused.text
    assert (await client.get("/api/v1/runs/missing/context-breakdown")).status_code == 404


async def _session_breakdowns(session_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AgentSessionEvent
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(AgentSessionEvent.payload)
            .where(AgentSessionEvent.session_id == session_id, AgentSessionEvent.type == "context.breakdown")
            .order_by(AgentSessionEvent.seq)
        )
        return [dict(payload) for payload in rows.scalars()]


async def test_a_sessions_calls_are_measured_with_numbers_only(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture
) -> None:
    await _agent(client)
    upstream.model.say("Order 42 shipped.").say("Friday.")
    upstream.model.usage = {"prompt_tokens": 640, "completion_tokens": 4}

    first = await _ask(client, f"Where is order 42? {CANARY}")
    session_id = first.json()["session_id"]
    (measured,) = await _session_breakdowns(session_id)
    await _window(measured["model"], 128_000)
    await _ask(client, "When?", session_id=session_id)

    breakdowns = await _session_breakdowns(session_id)
    assert [b["call"] for b in breakdowns] == [1, 2], "numbered across the session"
    assert all(b["prompt_tokens"] == 640 == sum(b["sections"].values()) for b in breakdowns)
    assert breakdowns[0]["sections"]["system"] > 0, "the agent's instructions"
    assert [b["max_input_tokens"] for b in breakdowns] == [None, 128_000]
    assert CANARY in json.dumps(upstream.model.requests[0][1]), "the person's words reach the model"
    assert CANARY not in json.dumps(breakdowns), "and no breakdown"
