"""A run writes its own lines of its journal, never the platform's (S65-08, ADR 0173).

The run's journal has two authors: the run (its ACP log, what its runner did) and the platform (the
verdict of each guarantee, `gate.outcome`; the memories it left out, `memory.skipped`). The console's
guarantees panel reads the `gate.outcome` lines as the platform's word. The run's own door took any
type, so a run could write its own verdicts.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .kits.closed_runs import a_run, close, token_of

RUNS = "/api/v1/internal/runs"

FORGED_VERDICT = {
    "seq": 7,
    "type": "gate.outcome",
    "payload": {"name": "no_secrets", "passed": True, "pending": False, "detail": "no secret in the diff"},
}
OWN_LINE = {"seq": 8, "type": "session/update", "payload": {"text": "implementing"}}


async def _journal(run_id: str) -> list[tuple[int, str]]:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
    return sorted((row.seq, row.type) for row in rows)


async def test_a_run_cannot_write_a_verdict_in_its_journal(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await a_run(project)

    refused = await client.post(
        f"{RUNS}/{run_id}/events", headers=token_of(run_id), json={"events": [FORGED_VERDICT]}
    )

    assert refused.status_code == 422, refused.text
    assert "gate.outcome" in refused.json()["detail"]
    assert await _journal(run_id) == []


async def test_a_batch_that_carries_one_is_refused_whole(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Nothing of the batch is written: a journal is not half a run's and half a forgery."""
    run_id = await a_run(project)

    refused = await client.post(
        f"{RUNS}/{run_id}/events", headers=token_of(run_id), json={"events": [OWN_LINE, FORGED_VERDICT]}
    )

    assert refused.status_code == 422, refused.text
    assert await _journal(run_id) == []


@pytest.mark.parametrize(
    "reserved",
    ["gate.outcome", "gate.later", "memory.skipped", "memory.recalled", " Gate.Outcome ", "GATE.OUTCOME"],
)
async def test_the_platforms_namespaces_are_refused_whole(
    client: AsyncClient, project: dict[str, Any], reserved: str
) -> None:
    run_id = await a_run(project)

    refused = await client.post(
        f"{RUNS}/{run_id}/events",
        headers=token_of(run_id),
        json={"events": [{"seq": 1, "type": reserved, "payload": {}}]},
    )

    assert refused.status_code == 422, refused.text
    assert await _journal(run_id) == []


@pytest.mark.parametrize(
    "own",
    [
        "session/update",
        "session/request_permission",
        "run.result",
        "run.failed",
        "git.push",
        "shim.refused",
        "tools.script.call",
        "outputs.stored",
        "context.reduction",
        "agent.confinement",
        "gateway",  # no namespace at all
        "gated.release",  # shares letters with a reserved namespace, not the namespace
        "memoryless",
    ],
)
async def test_a_run_still_writes_its_own_lines(
    client: AsyncClient, project: dict[str, Any], own: str
) -> None:
    run_id = await a_run(project)

    accepted = await client.post(
        f"{RUNS}/{run_id}/events",
        headers=token_of(run_id),
        json={"events": [{"seq": 1, "type": own, "payload": {}}]},
    )

    assert (accepted.status_code, accepted.json()) == (202, {"accepted": 1}), accepted.text
    assert await _journal(run_id) == [(1, own)]


async def test_a_closed_run_cannot_write_one_either(client: AsyncClient, project: dict[str, Any]) -> None:
    """The journal of a closed run still accepts (ADR 0143): the run's lines, not the platform's."""
    run_id = await a_run(project)
    await close(run_id)

    refused = await client.post(
        f"{RUNS}/{run_id}/events", headers=token_of(run_id), json={"events": [FORGED_VERDICT]}
    )
    accepted = await client.post(
        f"{RUNS}/{run_id}/events", headers=token_of(run_id), json={"events": [OWN_LINE]}
    )

    assert refused.status_code == 422, refused.text
    assert accepted.status_code == 202, accepted.text
    assert await _journal(run_id) == [(8, "session/update")]
