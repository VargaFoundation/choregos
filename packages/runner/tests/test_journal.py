"""The runner's ACP journal: in batches, except what the console shows live (S25-01)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from choregos_runner.client import InternalClient
from choregos_runner.runner import EventJournal


class _Client:
    """The internal client, reduced to what the journal calls: each batch sent is kept."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        self.batches.append(list(events))
        return len(events)


def _journal(tmp_path: Path) -> tuple[EventJournal, _Client]:
    client = _Client()
    return EventJournal(client=cast(InternalClient, client), path=tmp_path / "journal.jsonl"), client


def _update(kind: str, **fields: Any) -> dict[str, Any]:
    return {"sessionId": "s1", "update": {"sessionUpdate": kind, **fields}}


def _message(text: str) -> dict[str, Any]:
    return _update("agent_message_chunk", content={"type": "text", "text": text})


async def test_a_message_waits_for_its_batch(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    for i in range(3):
        await journal.record("session/update", _message(str(i)))
    assert client.batches == [], "three messages stay in the buffer"
    await journal.flush()
    assert [e["seq"] for e in client.batches[0]] == [1, 2, 3]


async def test_a_plan_leaves_without_waiting_with_what_was_waiting(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    await journal.record("session/update", _message("je lis"))
    entries = [{"content": "read the code", "priority": "high", "status": "in_progress"}]
    await journal.record("session/update", _update("plan", entries=entries))
    assert len(client.batches) == 1, "the plan left at once"
    assert [e["seq"] for e in client.batches[0]] == [1, 2], "with the message that preceded it, in order"
    assert client.batches[0][1]["payload"]["update"]["entries"] == entries
    # Nothing is lost or doubled: the transcript has both events.
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


async def test_a_full_batch_always_leaves(tmp_path: Path) -> None:
    journal, client = _journal(tmp_path)
    journal.batch_size = 2
    await journal.record("run.started", {"role": "implement"})
    await journal.record("session/update", _update("tool_call", toolCallId="t1", title="read"))
    assert [len(batch) for batch in client.batches] == [2]
