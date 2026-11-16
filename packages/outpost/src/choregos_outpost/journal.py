# SPDX-License-Identifier: Apache-2.0
"""The daemon's journal of call ids, persisted on disk (ADR 0149 §5, ADR 0148 §13).

One decision per call id. The first thing the daemon decides for an id, to make the call or to
refuse it, is written here and stands for every later envelope under that id, whoever sent it:

- an id that was refused is refused again with the same code;
- a call under way or done is answered from the journal, and its server is not called again;
- a write that was begun and left without an answer (the daemon was killed in the middle) is
  reported `unanswered`, and is never executed again.

A record reaches the disk before the daemon acts on it (`fsync`): a write's id is recorded before
the write is executed, and its answer after. What cannot be recorded is not done: every method
raises `JournalError` when the disk refuses, and the daemon then refuses the call (`daemon.py`).

Where it lives, under the journal directory (`<state dir>/journal`):

- `journal.jsonl`: one JSON line per step of a call (`refuse`, `take`, `begin`, `finish`, `ack`),
  folded into one entry per id when the file is read. It holds ids, names and digests, never
  arguments or results;
- `answers/<call id>`: the answer of a call, sealed to that call's `answer_key`, so that an id seen
  again before its deadline is answered from here. Only the platform can open it. It is deleted
  once the call's deadline has passed: the door takes no answer after it;
- `lock`: held by the daemon while it runs. Two daemons on one journal would each rewrite what the
  other recorded, so the second one does not start.

How it is bounded. An entry stands until its call's deadline, or 300 seconds after it was received
when its envelope was never opened; past that it is dropped, and the file is rewritten without it.
The one exception is a write that was begun: its id is kept for as long as the journal lives, so
that no later clock, set back by mistake or on purpose, can have it executed twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from choregos_contracts.enums import OutpostAnswerOutcome, OutpostRefusalCode
from choregos_contracts.outpost import MAX_LEASE_S, OUTPOST_ID_PATTERN

FILE = "journal.jsonl"
ANSWERS = "answers"
LOCK = "lock"
#: How long an id is kept when its envelope was not opened, so that its deadline is unknown.
UNOPENED_KEEP_S = MAX_LEASE_S
#: An entry that no longer stands is dropped this long after: a margin for a clock that moves.
DROP_AFTER_S = 60.0
#: The file is rewritten, one line per standing entry, once this many lines were appended to it.
COMPACT_EVERY = 2000

REFUSED, TAKEN, BEGUN, DONE = "refused", "taken", "begun", "done"


class JournalError(Exception):
    """The journal cannot be read, or the disk refused a record. Nothing is executed on top of it."""


@dataclass
class Entry:
    """What the journal holds for one call id."""

    id: str
    state: str
    #: Until when the entry stands, in seconds since the epoch: the call's deadline, or the moment it
    #: was received plus `UNOPENED_KEEP_S` when its envelope was not opened.
    keep_until: float
    refusal: str | None = None
    outcome: str | None = None
    #: What the outpost read from its own server: the tool does not declare itself read-only.
    write: bool | None = None
    #: The key the call's answer is sealed to: what lets a restarted daemon report `unanswered`.
    answer_key: str | None = None
    kind: str | None = None
    endpoint: str | None = None
    tool: str | None = None
    arguments_sha256: str | None = None
    signed_by: str | None = None
    #: A sealed answer is held under `answers/`.
    held: bool = False
    #: The door recorded the answer or the refusal.
    acknowledged: bool = False

    @property
    def permanent(self) -> bool:
        """A write that was begun: its id is never dropped."""
        return self.write is True and self.state in (BEGUN, DONE)

    @property
    def under_way(self) -> bool:
        return self.state in (TAKEN, BEGUN)

    def stands(self, now: float) -> bool:
        """Whether this entry still decides what a later envelope under its id gets."""
        return self.permanent or self.under_way or now < self.keep_until


_FIELDS = {field.name for field in fields(Entry)}


def _fold(entries: dict[str, Entry], record: dict[str, Any]) -> None:
    """Applies one line of the file to the entries."""
    operation, call_id = record.get("op"), record.get("id")
    if not isinstance(call_id, str) or not re.fullmatch(OUTPOST_ID_PATTERN, call_id):
        raise ValueError("a record without a call id")
    known = {name: value for name, value in record.items() if name in _FIELDS and name not in ("id", "state")}
    if operation in ("entry", "take"):
        state = str(record["state"]) if operation == "entry" else TAKEN
        entries[call_id] = Entry(id=call_id, state=state, **known)
        return
    if operation == "refuse":
        former = entries.get(call_id)
        kept = asdict(former) if former is not None and former.under_way else {"id": call_id}
        entries[call_id] = Entry(**{**kept, **known, "state": REFUSED, "refusal": str(record["refusal"])})
        return
    entry = entries.get(call_id)
    if entry is None:
        if operation == "ack":
            return  # the door confirmed what was dropped meanwhile: nothing is left to mark
        raise ValueError(f"`{operation}` for a call that was never taken")
    if operation == "begin":
        entry.state, entry.write = BEGUN, bool(record["write"])
    elif operation == "finish":
        entry.state, entry.outcome, entry.held = DONE, str(record["outcome"]), bool(record.get("held"))
    elif operation == "ack":
        entry.acknowledged = True
    else:
        raise ValueError(f"unknown operation `{operation}`")


class Journal:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / FILE
        self._answers = directory / ANSWERS
        self._entries: dict[str, Entry] = {}
        self._appended = 0
        self._held: int | None = None
        #: Whether the file ended on a record the disk never confirmed when it was read.
        self.torn = False
        self._lock = asyncio.Lock()

    # ───────────────────────── reading ─────────────────────────

    def _take_lock(self) -> None:
        descriptor = os.open(self.directory / LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            raise JournalError(
                f"{self.directory} is held by another daemon: one instance, one journal. Stop the other "
                "one, or give this one a state directory and an identity of its own."
            ) from error
        self._held = descriptor

    def close(self) -> None:
        """Lets go of the journal, so that another process may hold it."""
        if self._held is not None:
            os.close(self._held)
            self._held = None

    def _read(self, now: float | None) -> None:
        self._answers.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        if self._held is None:
            self._take_lock()
        entries: dict[str, Entry] = {}
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        lines = text.split("\n")
        # A last line without its newline is a record the disk never confirmed: whatever it announced
        # was not done, since the daemon acts only once a record is written.
        torn = lines.pop() if lines else ""
        for number, line in enumerate(lines, start=1):
            try:
                _fold(entries, json.loads(line))
            except (ValueError, KeyError, TypeError) as error:
                raise JournalError(
                    f"{self.path}, line {number}: not a journal record ({error}). The journal says which "
                    "writes were made: it is not guessed at. Restore the file, or enrol a new instance."
                ) from error
        for entry in entries.values():
            entry.held = entry.held and (self._answers / entry.id).exists()
        # An answer no entry holds any more (its call was taken again and refused, or a write of it
        # was cut short) is nobody's: it is not kept.
        for kept in self._answers.iterdir():
            holder = entries.get(kept.name)
            if holder is None or not holder.held:
                kept.unlink()
        self._entries = entries
        if now is not None:
            self._drop(now)
        # Rewritten at once: one line per entry that is kept, and the torn line, if any, is gone.
        self.torn = bool(torn)
        self._rewrite()

    def _rewrite(self) -> None:
        """One line per entry, written beside the file and moved over it."""
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            for entry in self._entries.values():
                file.write(json.dumps({"op": "entry", **asdict(entry)}, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.path)
        self._sync_directory(self.directory)
        self._appended = 0

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def open(self, now: float | None = None) -> None:
        """Reads what a former process left, without what lapsed before `now`; `JournalError` when the
        journal cannot be read, written, or is another daemon's."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._read, now)
            except OSError as error:
                raise JournalError(f"{self.directory}: {error.strerror or error}") from error

    def get(self, call_id: str) -> Entry | None:
        return self._entries.get(call_id)

    def standing(self, call_id: str, now: float) -> Entry | None:
        """The entry that decides for this id, or None when the id is new or its entry lapsed."""
        entry = self._entries.get(call_id)
        return entry if entry is not None and entry.stands(now) else None

    def under_way(self) -> list[Entry]:
        """Calls taken or begun and not finished: at start, what a former process left in the middle."""
        return [entry for entry in self._entries.values() if entry.under_way]

    def unsettled(self, now: float) -> list[Entry]:
        """Answers held and refusals that the door never confirmed, for calls whose deadline is still
        ahead: what a restarted daemon sends again."""
        return [
            entry
            for entry in self._entries.values()
            if not entry.acknowledged
            and now < entry.keep_until
            and (entry.state == REFUSED or (entry.state == DONE and entry.held))
        ]

    def __len__(self) -> int:
        return len(self._entries)

    # ───────────────────────── writing ─────────────────────────

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps({"at": datetime.now(UTC).isoformat(), **record}, separators=(",", ":")) + "\n"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def _record(self, record: dict[str, Any]) -> Entry:
        """Writes a record to the disk, then applies it: never the other way round."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._append, record)
            except OSError as error:
                raise JournalError(
                    f"{self.path}: the record could not be written ({error.strerror or error})"
                ) from error
            _fold(self._entries, record)
            self._appended += 1
            return self._entries[str(record["id"])]

    def _known(self, call_id: str) -> None:
        """A step of a call the journal does not hold is a fault of the daemon, caught before it is
        written: a line the next start could not read would stop the outpost."""
        if call_id not in self._entries:
            raise JournalError(
                f"{self.path}: no call `{call_id}` was taken: nothing is recorded on top of it"
            )

    async def refuse(
        self, call_id: str, code: OutpostRefusalCode, *, keep_until: float, **known: Any
    ) -> Entry:
        """The decision to refuse an id: it is refused again with this code while the entry stands."""
        record = {"op": "refuse", "id": call_id, "refusal": code.value, "keep_until": keep_until}
        return await self._record(
            {**record, **{name: value for name, value in known.items() if value is not None}}
        )

    async def take(self, call_id: str, *, keep_until: float, **known: Any) -> Entry:
        """The decision to make a call: from here on, no other envelope under this id is made."""
        return await self._record({"op": "take", "id": call_id, "keep_until": keep_until, **known})

    async def begin(self, call_id: str, *, write: bool) -> Entry:
        """Recorded before the endpoint's server is called. For a write, this line is what keeps a
        restarted daemon from executing it again."""
        self._known(call_id)
        return await self._record({"op": "begin", "id": call_id, "write": write})

    async def finish(self, call_id: str, outcome: OutpostAnswerOutcome, *, sealed: str | None) -> Entry:
        """Recorded after the server was called: the outcome, and the sealed answer to give again."""
        self._known(call_id)
        held = sealed is not None
        if sealed is not None:
            async with self._lock:
                try:
                    await asyncio.to_thread(self._hold, call_id, sealed)
                except OSError as error:
                    raise JournalError(
                        f"{self._answers}: the answer could not be kept ({error.strerror or error})"
                    ) from error
        return await self._record({"op": "finish", "id": call_id, "outcome": outcome.value, "held": held})

    async def acknowledge(self, call_id: str) -> None:
        """The door took the answer or the refusal. An entry that lapsed meanwhile needs no mark."""
        if call_id in self._entries:
            await self._record({"op": "ack", "id": call_id})

    def _hold(self, call_id: str, sealed: str) -> None:
        temporary = self._answers / f"{call_id}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as file:
            file.write(sealed)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self._answers / call_id)

    async def answer(self, call_id: str) -> str | None:
        """The sealed answer held for this id, or None when none is held any more."""
        entry = self._entries.get(call_id)
        if entry is None or not entry.held:
            return None
        try:
            return await asyncio.to_thread((self._answers / call_id).read_text, "ascii")
        except OSError:
            return None

    # ───────────────────────── bounding ─────────────────────────

    def _drop(self, now: float) -> None:
        """Deletes the answers whose deadline has passed, and forgets the entries that lapsed."""
        for entry in self._entries.values():
            if entry.held and not entry.under_way and now >= entry.keep_until:
                with contextlib.suppress(FileNotFoundError):
                    (self._answers / entry.id).unlink()
                entry.held = False
        lapsed = [
            call_id
            for call_id, entry in self._entries.items()
            if not entry.stands(now) and now >= entry.keep_until + DROP_AFTER_S
        ]
        for call_id in lapsed:
            del self._entries[call_id]

    def _sweep(self, now: float) -> None:
        self._drop(now)
        if self._appended >= COMPACT_EVERY:
            self._rewrite()

    async def sweep(self, now: float) -> None:
        """Deletes the answers whose deadline has passed, drops the entries that no longer stand, and
        rewrites the file when it has grown. A write that was begun is never dropped."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._sweep, now)
            except OSError as error:
                raise JournalError(f"{self.directory}: {error.strerror or error}") from error
