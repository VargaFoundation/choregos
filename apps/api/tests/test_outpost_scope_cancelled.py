"""A look at an outpost's call that a cancellation interrupts stays a cancellation (S65-29).

`scope_to_outpost` reads an outpost under the all-organisations scope, then narrows the session to the
outpost's organisation. On PostgreSQL a task cancelled in the middle of that read (a worker stopping
while it waits for a write, `rendezvous.wait`) leaves a connection SQLAlchemy has invalidated: any
further statement raises `PendingRollbackError`. The narrowing used to run in a `finally`, so its error
replaced the cancellation, and a worker stopped at shutdown reported a database error instead. Seen on
CI's PostgreSQL job as `test_a_worker_that_stops_while_its_write_is_claimed_does_not_put_it_in_doubt`
raising `PendingRollbackError`, depending on the instant the cancellation landed. Here the instant is
fixed: the read itself is cancelled, by a session that behaves as an invalidated one afterwards.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.sql.elements import TextClause


class _InterruptedSession:
    """Statements of text (the two settings) pass; the read is cancelled; after it, as after a
    cancellation that reached the driver, every statement raises `PendingRollbackError`."""

    def __init__(self) -> None:
        self.interrupted = False
        self.after: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> Any:
        if self.interrupted:
            self.after.append(str(statement))
            raise PendingRollbackError("Can't reconnect until invalid transaction is rolled back.")
        if isinstance(statement, TextClause):
            return SimpleNamespace(first=lambda: None)
        self.interrupted = True
        raise asyncio.CancelledError


async def test_a_read_cancelled_in_the_middle_raises_the_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.db import session as db_session
    from choregos_api.services import outposts

    # The settings are PostgreSQL's: under SQLite the two settings are not statements at all.
    monkeypatch.setattr(db_session, "get_settings", lambda: SimpleNamespace(is_sqlite=False))
    session = _InterruptedSession()
    with pytest.raises(asyncio.CancelledError):
        await outposts.scope_to_outpost(session, "outpost-1")  # type: ignore[arg-type]
    assert session.after == [], "nothing runs on a transaction the cancellation made unusable"


async def test_a_read_that_finds_the_outpost_narrows_the_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The way out is unchanged: the outpost found, the session is narrowed to its organisation."""
    from choregos_api.db import session as db_session
    from choregos_api.services import outposts

    monkeypatch.setattr(db_session, "get_settings", lambda: SimpleNamespace(is_sqlite=False))
    said: list[dict[str, Any]] = []
    outpost = SimpleNamespace(id="outpost-1")

    class _Session:
        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
            if isinstance(statement, TextClause):
                said.append(dict(params or {}))
                return SimpleNamespace(first=lambda: None)
            return SimpleNamespace(first=lambda: (outpost, "acme"))

    found = await outposts.scope_to_outpost(_Session(), "outpost-1")  # type: ignore[arg-type]
    assert found == (outpost, "acme")
    assert said[-1] == {"orgs": "acme"}, said
