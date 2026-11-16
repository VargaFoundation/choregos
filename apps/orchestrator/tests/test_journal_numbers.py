"""The platform's lines of a run's journal take numbers the run cannot take (S65-28, finding #1608).

A run's journal keeps one line per number, the first one it receives (`POST /internal/runs/{id}/events`).
The runner numbers its lines from 1, and the door refuses a number below 0. The orchestrator wrote the
memories it left out (`memory.skipped`) before the run started at "the highest number plus one", that is
from 1 on an empty journal: the runner's first lines were then dropped, silently (a 202 with a smaller
`accepted`). They now take negative numbers; the verdicts (`gate.outcome`), written once the run's process
has finished, still follow the highest line, never below 1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from .conftest import Fixture

API = "http://journal.test/api/v1/internal"


async def _agent(setup: Fixture, memories: list[dict[str, Any]]) -> None:
    """The agent `architect`, version 1, bound to `memories`."""
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug="architect",
            kind="internal",
            display_name="architect",
            status="active",
            scope="org",
        )
        session.add(agent)
        await session.flush()
        spec = {
            "instructions": "Review {{ ticket.title }} and report.",
            "model": "profile:standard",
            "budget": {"run_usd": 0.5},
            "memories": memories,
        }
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=spec, checksum="v1")
        )


def _plan(setup: Fixture) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
        "agent": "architect",
    }


async def _run_of(setup: Fixture, run_id: str) -> str:
    """A run row of the work item, with no line in its journal."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                agent_slug="architect",
                stage_input={"memory_scope": "none", "memories": []},
            )
        )
    return run_id


@pytest.fixture
async def api(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The platform's API on the suite's database: the run's door and the console's route, with the
    development sign-in the API's own suite uses."""
    import httpx

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    monkeypatch.setenv("CHOREGOS_SESSION_SECRET", "test-session-secret-" + "x" * 32)
    from choregos_api.config import reset_settings_cache as reset_api_settings
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    reset_api_settings()
    set_temporal(FakeTemporal())
    yield httpx.ASGITransport(app=create_app())
    set_temporal(None)


def _token(run_id: str) -> str:
    from choregos_api.security import mint_run_token

    return mint_run_token(
        run_id, project_slug="billing-api", work_item_key="varga/billing-api#123", ttl_minutes=30
    )


async def _journal_as_the_console_reads_it(transport: Any, run_id: str) -> list[tuple[int, str]]:
    """`GET /runs/{id}/events`, as a person signed in to the console."""
    import httpx

    async with httpx.AsyncClient(transport=transport, base_url="http://journal.test") as http:
        start = await http.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
        assert start.status_code == 307, start.text
        callback = await http.get(start.headers["location"])
        assert callback.status_code == 307, callback.text
        answer = await http.get(f"/api/v1/runs/{run_id}/events")
    assert answer.status_code == 200, answer.text
    return [(line["seq"], line["type"]) for line in answer.json()]


async def _stored(run_id: str) -> list[tuple[int, str]]:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
            .scalars()
            .all()
        )
    return [(row.seq, row.type) for row in rows]


async def test_a_run_prepared_with_a_skipped_memory_keeps_its_runners_first_lines(
    setup: Fixture, api: Any, tmp_path: Any
) -> None:
    """The issue's test: one skipped memory, the runner posts lines 1 to 3, the journal shows four.

    Red before the change: `memory.skipped` took 1, the door kept it and dropped the runner's line 1
    (`accepted` 2, three lines)."""
    from choregos_orchestrator.activities.stage import prepare_stage
    from choregos_runner.runner import EventJournal
    from choregos_tools_mcp.client import InternalClient

    await _agent(
        setup,
        [
            {"scope": "global", "slug": "never-created", "mode": "read"},
            {"scope": "project", "slug": "shared", "mode": "write"},
        ],
    )
    prepared = await prepare_stage(_plan(setup))
    run_id = prepared["run_id"]

    # The runner's own journal, as it runs: numbered from 1, posted to the run's door with the run's token.
    client = InternalClient(API, run_id, _token(run_id), transport=api)
    journal = EventJournal(client=client, path=tmp_path / "transcript.jsonl")
    for kind in ("session/new", "session/update", "run.result"):
        await journal.record(kind, {})
    await journal.flush()
    await client.aclose()
    assert journal.seq == 3

    assert await _journal_as_the_console_reads_it(api, run_id) == [
        (-1, "memory.skipped"),
        (1, "session/new"),
        (2, "session/update"),
        (3, "run.result"),
    ]


async def test_skipped_memories_keep_their_order_below_zero_and_a_replay_adds_nothing(setup: Fixture) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import _journal_skipped_memories

    run_id = await _run_of(setup, "run-numbers")
    first = [
        {"memory": "global:gone", "reason": "the memory does not exist"},
        {"memory": "global:old", "reason": "the memory is archived"},
        {"memory": "global:gone", "reason": "the memory does not exist"},
    ]
    later = [{"memory": "project:elsewhere", "reason": "no memory `elsewhere` in this project"}]
    for batch in (first, first, later):
        async with session_scope() as session:
            await _journal_skipped_memories(session, run_id, batch)

    from choregos_api.db.models import RunEvent
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
            .scalars()
            .all()
        )
    assert [(row.seq, row.payload["memory"]) for row in rows] == [
        (-3, "project:elsewhere"),
        (-2, "global:gone"),
        (-1, "global:old"),
    ], "below every number a run can send, a batch in the order it was skipped, each memory once"


async def test_a_verdict_follows_the_runners_last_line_and_never_takes_zero(setup: Fixture) -> None:
    """`gate.outcome` is written once the run's process has finished: after the highest line, from 1."""
    from choregos_api.db.models import Run, RunEvent
    from choregos_api.db.session import session_scope
    from choregos_core import GateOutcome, utcnow
    from choregos_orchestrator.activities.gates import _journal
    from choregos_orchestrator.activities.stage import _journal_skipped_memories

    verdicts = [GateOutcome(name="tests_green", passed=True), GateOutcome(name="scope", passed=False)]
    skipped = [{"memory": "global:gone", "reason": "the memory does not exist"}]

    only_skipped = await _run_of(setup, "run-silent")
    with_lines = await _run_of(setup, "run-spoke")
    async with session_scope() as session:
        for run_id in (only_skipped, with_lines):
            await _journal_skipped_memories(session, run_id, skipped)
        for seq in (1, 2):
            session.add(RunEvent(run_id=with_lines, seq=seq, type="session/update", payload={}, ts=utcnow()))
    async with session_scope() as session:
        for run_id in (only_skipped, with_lines):
            run = await session.get(Run, run_id)
            assert run is not None
            await _journal(session, run, verdicts)

    assert await _stored(only_skipped) == [(-1, "memory.skipped"), (1, "gate.outcome"), (2, "gate.outcome")]
    assert await _stored(with_lines) == [
        (-1, "memory.skipped"),
        (1, "session/update"),
        (2, "session/update"),
        (3, "gate.outcome"),
        (4, "gate.outcome"),
    ]
