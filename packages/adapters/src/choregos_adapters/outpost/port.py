# SPDX-License-Identifier: Apache-2.0
"""The rendezvous, as whoever routes a call to an outpost sees it: a port (ADR 0149 §3-4).

A call through an outpost is not a connection: it is placed where an instance of the outpost will
claim it, awaited, then forgotten. The platform implements this port over its database
(`outpost_calls`, behind the outposts' door); `FakeOutpost` implements it in memory. The tool client
(`OutpostToolClient`) knows nothing else of either.

What the port promises (ADR 0149 §5):

- `place` writes the call, or takes none (`NotAdmitted`). A call that carries a `write_key` is
  placed once under it: placing it again returns the first call's id and delivers nothing a second
  time, whatever became of the first;
- `wait` returns what became of the call, within the time the call was placed for: it closes the
  call itself when that time is up;
- `cancel` says the platform stopped waiting. It does not interrupt a call an instance already
  made: a claimed write is in doubt from there on;
- `forget` empties what the rendezvous still holds of a call whose outcome was read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol

from choregos_contracts import OutpostAnswer, OutpostCallKind, OutpostCallState, OutpostRefusalCode

#: Why a rendezvous took no call. Nothing was written, and nothing will reach an outpost.
SWITCHED_OFF: Final = "switched_off"
PAUSED: Final = "paused"
REVOKED: Final = "revoked"
#: The deployment has no key to seal a call's arguments under while they wait.
NO_SECRETS_KEY: Final = "no_secrets_key"
#: No instance of the outpost that serves the alias polled within the presence window.
OFFLINE: Final = "offline"
OUTPOST_QUOTA: Final = "outpost_quota"
ORGANISATION_QUOTA: Final = "organisation_quota"
OUTPOST_RATE: Final = "outpost_rate"
ORGANISATION_RATE: Final = "organisation_rate"
TOO_LARGE: Final = "too_large"
ID_TAKEN: Final = "id_taken"
#: The call is not one the door carries: a timeout beyond the longest lease, an alias that is none.
INVALID_CALL: Final = "invalid_call"
#: A write is delivered at most once, under a key its caller gives: without one it is not placed.
WRITE_WITHOUT_KEY: Final = "write_without_key"

#: The reasons that say "this route is closed": an `OutpostPaused`.
CLOSED: Final = frozenset({SWITCHED_OFF, PAUSED, REVOKED, NO_SECRETS_KEY})
#: The reasons that pass by themselves: a place frees up, a minute goes by.
BUSY: Final = frozenset({OUTPOST_QUOTA, ORGANISATION_QUOTA, OUTPOST_RATE, ORGANISATION_RATE})


class NotAdmitted(Exception):  # noqa: N818 - a decision at admission, not a failure
    """The rendezvous takes no call: `reason` is one of the constants above."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Call:
    """A call to place: a tool call, or a tool listing (discovery). It names an alias, never an
    address; `asserted` is who the platform says the call is made for, which the outpost may log and
    decides nothing on (ADR 0149 §3)."""

    kind: OutpostCallKind
    endpoint: str
    #: How long the platform waits, the wait for an instance to claim the call included.
    timeout_s: float
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    asserted: Mapping[str, Any] = field(default_factory=dict)
    #: The key a write is placed once under (its effect's, ADR 0149 §5); `None` for a call that may
    #: be asked again.
    write_key: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of a call. `state` is never `pending` or `claimed`: whoever waits is handed an
    outcome once there is one."""

    state: OutpostCallState
    #: The rendezvous took the call for a write: it was delivered at most once.
    write: bool = False
    #: An answered call, its envelope open; `None` when the rendezvous no longer holds the answer.
    answer: OutpostAnswer | None = None
    #: A refused call: the outpost's code.
    refusal: OutpostRefusalCode | None = None
    #: The instance that claimed it and when: what an in-doubt write is shown with.
    instance_id: str | None = None
    claimed_at: datetime | None = None


class Rendezvous(Protocol):
    """Where a call meets the outpost that makes it."""

    @property
    def outpost(self) -> str:
        """The outpost's name, as refusals say it."""

    async def place(self, call: Call) -> str:
        """Place the call and return its id; `NotAdmitted` when none is taken."""

    async def wait(self, call_id: str) -> Outcome | None:
        """What became of the call, once it is answered, refused or out of time; `None` when the
        rendezvous has no such call any more."""

    async def cancel(self, call_id: str) -> None:
        """The platform stops waiting for the call."""

    async def forget(self, call_id: str) -> None:
        """Empty what is left of a call whose outcome was read."""


__all__ = [
    "BUSY",
    "CLOSED",
    "ID_TAKEN",
    "INVALID_CALL",
    "NO_SECRETS_KEY",
    "OFFLINE",
    "ORGANISATION_QUOTA",
    "ORGANISATION_RATE",
    "OUTPOST_QUOTA",
    "OUTPOST_RATE",
    "PAUSED",
    "REVOKED",
    "SWITCHED_OFF",
    "TOO_LARGE",
    "WRITE_WITHOUT_KEY",
    "Call",
    "NotAdmitted",
    "Outcome",
    "Rendezvous",
]
