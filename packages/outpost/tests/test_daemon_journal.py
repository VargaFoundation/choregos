# SPDX-License-Identifier: Apache-2.0
"""One decision per call id, kept on disk (ADR 0149 §5, ADR 0148 §13).

The journal is consulted first: an id it holds gets what it holds. A write's id is recorded before
the write is executed and its answer after; a daemon that died in the middle of a write reports it
`unanswered` and does not execute it again. The test that kills a real process is
`test_killed_daemon.py`; here the daemon is restarted over the journal a former one left.
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts.enums import OutpostAnswerOutcome, OutpostRefusalCode
from choregos_outpost.audit import AuditLog
from choregos_outpost.daemon import Exit
from choregos_outpost.journal import Journal, JournalError

from ._bench import Bench, bench


def _journal_lines(b: Bench) -> list[dict[str, Any]]:
    path = b.state / "journal" / "journal.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _line(b: Bench, call_id: str, event: str, count: int = 1) -> None:
    async with asyncio.timeout(5):
        while sum(1 for line in b.lines(call_id) if line["event"] == event) < count:
            await asyncio.sleep(0.01)


async def test_a_call_id_seen_twice_is_answered_from_the_journal(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    again = b.handed(
        "seen-twice",
        {
            "id": "seen-twice",
            "kind": "call_tool",
            "endpoint": "tracker",
            "tool": "create_issue",
            "arguments": {"title": "once"},
            "deadline": (datetime.now(UTC) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            "answer_key": b.identity.sealing_key,
        },
    )
    b.door.hand_as_is(again)
    await b.start()
    await _line(b, "seen-twice", "executed")
    # The same signed call, replayed to the same instance by whoever sits on the path.
    b.door.hand_as_is(again)
    await _line(b, "seen-twice", "replayed")
    await b.stop()
    assert b.arrived == ["create_issue"], "the server was called once"
    events = [(line["event"], line.get("outcome")) for line in b.lines("seen-twice")]
    assert events == [("received", None), ("executed", "result"), ("replayed", "result")]
    # What the replay is answered with is the journal's: the same answer, marked as replayed, sealed to
    # the key found inside the call.
    held = await _held_answer(b, "seen-twice")
    opened = b.identity.open("seen-twice", held)
    assert opened["outcome"] == "result" and opened["replayed"] is True and opened["write"] is True
    assert opened["result"]["content"][0]["text"] == "create_issue done"
    answers = [body for verb, claims, body in b.door.received if verb == "answer" and claims]
    assert len(answers) == 2 and json.loads(answers[1])["sealed"] == held


async def _held_answer(b: Bench, call_id: str) -> str:
    return (b.state / "journal" / "answers" / call_id).read_text(encoding="ascii")


async def test_an_id_that_was_refused_is_refused_again_with_the_same_code_and_never_made(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """The first decision for an id stands: a copy that does not verify comes first, the door's own call
    under the same id comes after, and it is not made."""
    b = await bench(tmp_path, daemon_log)
    real = b.handed("contested")
    forged = {key: value for key, value in real.items() if key != "signature"}
    b.door.hand_as_is(forged)
    await b.start()
    await _line(b, "contested", "refused")
    b.door.hand_as_is(real)
    await _line(b, "contested", "refused", count=2)
    await b.stop()
    lines = b.lines("contested")
    assert [(line["event"], line["refusal"]) for line in lines] == [("refused", "signature_refused")] * 2
    assert all("tool" not in line and "signed_by" not in line for line in lines)
    assert b.server.received == [], "a refused id is never made"


async def test_a_copy_that_does_not_verify_cannot_have_a_call_under_way_refused(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "create_issue", {"title": "under way"})
    await b.start()
    await b.reached()
    # The write is in flight. A copy of it arrives without the door's signature.
    b.door.hand_as_is({"id": call.id, "sealed": "A" * 120})
    await b.door.polled(2)
    assert call.id not in b.door.refusals
    b.gate.set()
    answer = await b.door.answered(call.id)
    await b.stop()
    assert answer.outcome == "result" and b.arrived == ["create_issue"]
    refusals = [claims["htu"] for verb, claims, _ in b.door.received if verb == "refuse" and claims]
    assert not refusals, "once it has accepted a call, a daemon sends no refusal for a copy of it"
    assert [line["event"] for line in b.lines(call.id)] == ["received", "executed"]


async def test_a_writes_id_is_on_disk_before_the_write_is_executed_and_its_answer_after(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "create_issue", {"title": "ordered"})
    await b.start()
    await b.reached()
    # The server holds the call: what the disk says at this very moment.
    during = [(line["op"], line.get("write")) for line in _journal_lines(b) if line["id"] == call.id]
    assert during == [("take", None), ("begin", True)]
    assert not (b.state / "journal" / "answers" / call.id).exists()
    assert [line["event"] for line in b.lines(call.id)] == ["received"]
    b.gate.set()
    await b.door.answered(call.id)
    await b.stop()
    after = [line["op"] for line in _journal_lines(b) if line["id"] == call.id]
    assert after == ["take", "begin", "finish", "ack"]
    assert (b.state / "journal" / "answers" / call.id).exists()


async def test_a_daemon_restarted_in_the_middle_of_a_write_reports_it_unanswered_and_never_makes_it_again(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "create_issue", {"title": "interrupted"}, lease_s=120)
    await b.start()
    await b.reached()
    # The process dies here: the write reached the server, and no answer was recorded.
    await b.kill()
    assert [line["op"] for line in _journal_lines(b) if line["id"] == call.id] == ["take", "begin"]

    b.gate = None
    await b.start()
    answer = await b.door.answered(call.id)
    await b.stop()
    assert (
        answer.outcome == OutpostAnswerOutcome.UNANSWERED and answer.replayed is True and answer.write is True
    )
    assert answer.result is None
    assert b.arrived == ["create_issue"], "the write was not executed again"
    assert [(line["event"], line.get("outcome")) for line in b.lines(call.id)] == [
        ("received", None),
        ("replayed", "unanswered"),
    ]
    assert b.logged("call.left_unanswered")[0]["call_id"] == call.id

    # And it stays so: handed the same call again, a third daemon answers from the journal.
    b.door.hand_as_is(b.handed(call.id, {**call.plaintext, "deadline": "2099-01-01T00:00:00Z"}))
    await b.start()
    await _line(b, call.id, "replayed", count=2)
    await b.stop()
    assert b.arrived == ["create_issue"]


async def test_a_call_taken_and_never_begun_is_refused_after_a_restart(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """Its server received nothing, so it is a refusal and not a doubt."""
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "create_issue", {"title": "never begun"}, lease_s=120)
    # A former process recorded that it took the call, and died before it began it.
    journal = Journal(b.state / "journal")
    await journal.open()
    b.door.instances[b.instance_id].sequence = 0
    claimed = b.door._hand(b.door.instances[b.instance_id], 1)
    assert [entry["id"] for entry in claimed] == [call.id]
    await journal.take(
        call.id, keep_until=datetime.now(UTC).timestamp() + 120, answer_key=call.plaintext["answer_key"]
    )
    journal.close()

    await b.start()
    assert await b.door.refused(call.id) == "busy"
    await b.stop()
    assert b.server.received == []
    assert [(line["event"], line["refusal"]) for line in b.lines(call.id)] == [("refused", "busy")]


async def test_a_read_is_made_again_once_its_deadline_has_passed_and_a_write_never(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    clock = [datetime.now(UTC).timestamp()]

    def plaintext(call_id: str, tool: str, ahead: float) -> dict[str, Any]:
        deadline = datetime.fromtimestamp(clock[0] + ahead, UTC).isoformat().replace("+00:00", "Z")
        return {
            "id": call_id,
            "kind": "call_tool",
            "endpoint": "tracker",
            "tool": tool,
            "arguments": {},
            "deadline": deadline,
            "answer_key": b.identity.sealing_key,
        }

    b.door.hand_as_is(b.handed("a-read", plaintext("a-read", "search_issues", 30)))
    b.door.hand_as_is(b.handed("a-write", plaintext("a-write", "create_issue", 30)))
    await b.start(now=lambda: clock[0])
    await _line(b, "a-read", "executed")
    await _line(b, "a-write", "executed")
    assert sorted(b.arrived) == ["create_issue", "search_issues"]

    # Both deadlines are behind now. The door hands the read over again with a later one: it is made
    # again. The write's envelope comes back too, replayed with a clock that says it is still fresh.
    clock[0] += 400
    b.door.hand_as_is(b.handed("a-read", plaintext("a-read", "search_issues", 30)))
    await _line(b, "a-read", "executed", count=2)
    clock[0] -= 400
    b.door.hand_as_is(b.handed("a-write", plaintext("a-write", "create_issue", 30)))
    await _line(b, "a-write", "refused")
    await b.stop()
    assert sorted(b.arrived) == ["create_issue", "search_issues", "search_issues"]
    assert b.lines("a-write")[-1]["refusal"] == "deadline_passed"


async def test_the_journal_drops_what_lapsed_and_keeps_every_write_that_was_begun(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal")
    await journal.open()
    now = 1_000_000.0
    await journal.refuse("refused", OutpostRefusalCode.TOOL_NOT_ALLOWED, keep_until=now + 30)
    for call_id, write in (("read", False), ("write", True)):
        await journal.take(call_id, keep_until=now + 30, answer_key="k")
        await journal.begin(call_id, write=write)
        await journal.finish(call_id, OutpostAnswerOutcome.RESULT, sealed="sealed-" + call_id)
    assert {
        entry.id for entry in (journal.standing(i, now) for i in ("refused", "read", "write")) if entry
    } == {
        "refused",
        "read",
        "write",
    }
    assert await journal.answer("read") == "sealed-read"

    later = now + 31
    assert journal.standing("refused", later) is None and journal.standing("read", later) is None
    assert journal.standing("write", later) is not None, "a write that was begun always stands"
    await journal.sweep(later)
    assert await journal.answer("read") is None and await journal.answer("write") is None
    assert not list((tmp_path / "journal" / "answers").iterdir()), "no answer is kept past its deadline"

    await journal.sweep(now + 3600)
    assert len(journal) == 1 and journal.get("write") is not None
    journal.close()

    # What a restart reads is what was kept.
    again = Journal(tmp_path / "journal")
    await again.open(now + 3600)
    assert len(again) == 1 and again.standing("write", now + 10**9) is not None
    assert (tmp_path / "journal" / "journal.jsonl").read_text(encoding="utf-8").count("\n") == 1
    again.close()


async def test_a_torn_last_line_is_a_record_that_was_never_confirmed(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal")
    await journal.open()
    await journal.take("kept", keep_until=9e12, answer_key="k")
    journal.close()
    path = tmp_path / "journal" / "journal.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"op":"begin","id":"kept","wri', encoding="utf-8")
    again = Journal(tmp_path / "journal")
    await again.open()
    assert again.torn and again.get("kept") is not None and again.get("kept").state == "taken"  # type: ignore[union-attr]
    again.close()


async def test_a_journal_that_does_not_read_is_not_guessed_at(tmp_path: Path) -> None:
    directory = tmp_path / "journal"
    directory.mkdir()
    (directory / "journal.jsonl").write_text(
        '{"op":"take","id":"a","keep_until":1}\nnot a record\n', encoding="utf-8"
    )
    with pytest.raises(JournalError, match="line 2"):
        await Journal(directory).open()


async def test_one_journal_is_held_by_one_daemon(tmp_path: Path) -> None:
    first = Journal(tmp_path / "journal")
    await first.open()
    with pytest.raises(JournalError, match="held by another daemon"):
        await Journal(tmp_path / "journal").open()
    first.close()
    second = Journal(tmp_path / "journal")
    await second.open()
    second.close()


async def test_a_daemon_that_cannot_record_a_call_does_not_make_it(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """The disk refuses the journal: the write is refused `busy`, its server receives nothing, and the
    daemon stops serving."""
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "create_issue", {"title": "unrecorded"})
    await b.start()
    assert b.journal is not None

    def refuse(_record: dict[str, Any]) -> None:
        raise OSError(28, "No space left on device")

    b.journal._append = refuse  # type: ignore[method-assign]
    assert await b.door.refused(call.id) == "busy"
    assert await b.ended() is Exit.CANNOT_RECORD
    assert b.server.received == [], "what cannot be recorded is not made"
    assert [(line["event"], line.get("refusal")) for line in b.lines(call.id)] == [
        ("received", None),
        ("refused", "busy"),
    ]
    said = b.logged("outpost.cannot_record")
    assert said and "No space left on device" in said[0]["reason"]


async def test_a_daemon_that_cannot_write_its_audit_log_does_not_make_the_call(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "create_issue", {"title": "unaudited"})
    audit = AuditLog(b.audit_path)

    def refuse(_text: str) -> None:
        raise OSError(30, "Read-only file system")

    audit._write = refuse  # type: ignore[method-assign]
    await b.start(audit=audit)
    assert await b.door.refused(call.id) == "busy"
    assert await b.ended() is Exit.CANNOT_RECORD
    assert b.server.received == []
    # The line that was lost is not lost in silence: the daemon's own log carries it, digests only.
    lost = b.logged("audit.line_lost")
    assert lost and json.loads(lost[0]["line"])["call_id"] == call.id
    assert json.loads(lost[0]["line"])["event"] == "received" and "unaudited" not in lost[0]["line"]


async def test_a_step_of_a_call_the_journal_does_not_hold_is_never_written(tmp_path: Path) -> None:
    """A line the next start could not read would stop the outpost: it is refused before it is written,
    and a confirmation that arrives after its entry lapsed needs no line at all."""
    journal = Journal(tmp_path / "journal")
    await journal.open()
    await journal.refuse("lapsed", OutpostRefusalCode.DEADLINE_PASSED, keep_until=100.0)
    await journal.sweep(1_000.0)
    assert journal.get("lapsed") is None
    await journal.acknowledge("lapsed")
    for step in (
        journal.begin("never-taken", write=True),
        journal.finish("never-taken", OutpostAnswerOutcome.RESULT, sealed="x"),
    ):
        with pytest.raises(JournalError, match="no call `never-taken` was taken"):
            await step
    journal.close()
    again = Journal(tmp_path / "journal")
    await again.open()  # it reads: nothing was written that it cannot fold
    assert not list((tmp_path / "journal" / "answers").iterdir())
    again.close()


async def test_an_answer_no_entry_holds_is_not_kept_across_a_restart(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal")
    await journal.open()
    await journal.take("held", keep_until=9e12, answer_key="k")
    await journal.begin("held", write=False)
    await journal.finish("held", OutpostAnswerOutcome.RESULT, sealed="sealed-answer")
    journal.close()
    answers = tmp_path / "journal" / "answers"
    (answers / "nobodys").write_text("left behind", encoding="ascii")
    (answers / "held.tmp").write_text("cut short", encoding="ascii")
    again = Journal(tmp_path / "journal")
    await again.open()
    assert sorted(path.name for path in answers.iterdir()) == ["held"]
    assert await again.answer("held") == "sealed-answer"
    again.close()
