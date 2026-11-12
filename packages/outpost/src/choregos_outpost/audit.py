# SPDX-License-Identifier: Apache-2.0
"""The outpost's own audit log (ADR 0147 §8): one JSON line per assignment received, executed,
refused or answered from the journal.

It is the operator's record, independent of the platform's, so that the two can be compared: a line
carries the call's id, the alias, the tool, the digest of the arguments the platform's `tool.called`
event keeps for the same call, the outcome and the names the control plane asserted. It holds
digests, never arguments or results. A call refused for its signature has one line, which says
nothing of what its envelope held: it was not opened.

Every line is an `OutpostAuditLine` of the contract, so a line that the schema would refuse cannot
be written. The file is opened for appending at each line, so that an operator who rotates it by
renaming loses nothing, and each line is flushed to the disk before the daemon goes on.

A line that cannot be written is never dropped in silence: `append` raises `AuditWriteError`, and
the daemon then refuses to execute what it could not record (`daemon.py`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from choregos_contracts.enums import OutpostAuditEvent
from choregos_contracts.outpost import OutpostAssertedNames, OutpostAuditLine, OutpostLoggedNames


class AuditWriteError(Exception):
    """The audit log refused a line. `line` is what was not written: digests, never a payload."""

    def __init__(self, path: Path, line: str, cause: OSError) -> None:
        self.path = path
        self.line = line
        super().__init__(f"{path}: the audit line could not be written ({cause.strerror or cause})")


def logged_names(asserted: OutpostAssertedNames | None) -> OutpostLoggedNames:
    """The asserted names a line keeps: the ones the contract lists, as the control plane wrote them.
    They are assertions, logged as such; the daemon decides nothing on them (ADR 0147 §6)."""
    if asserted is None:
        return OutpostLoggedNames()
    # `OutpostAssertedNames` reads a call leniently and drops a name this contract does not list; the
    # line's own model then refuses anything else.
    return OutpostLoggedNames.model_validate(asserted.model_dump())


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(descriptor, text.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def check(self) -> None:
        """Opens the file for appending without writing a line; `AuditWriteError` when it cannot be."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._write, "")
            except OSError as error:
                raise AuditWriteError(self.path, "", error) from error

    async def append(self, event: OutpostAuditEvent, call_id: str, **fields: Any) -> OutpostAuditLine:
        """Writes one line and returns it; `AuditWriteError` when the disk refuses it."""
        line = OutpostAuditLine(at=datetime.now(UTC), event=event, call_id=call_id, **fields)
        text = line.model_dump_json(exclude_none=True, exclude_defaults=True)
        async with self._lock:
            try:
                await asyncio.to_thread(self._write, text + "\n")
            except OSError as error:
                raise AuditWriteError(self.path, text, error) from error
        return line
