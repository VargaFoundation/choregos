# SPDX-License-Identifier: Apache-2.0
"""Why a call through an outpost gave no result: typed refusals (ADR 0149 §7).

A class per code, in the manner of the MCP gateway's refusals (ADR 0105 §7): never a crash, never a
500. Each names the outpost and the alias it was for, and says whether the outpost's server may have
received the call, which is what decides whether the call is booked: a call that reached no server
is not (ADR 0149 §7).

| Refusal | When | At the outpost's server? |
|:--|:--|:--|
| `OutpostPaused` | the outpost is paused or revoked, or outposts are switched off | no: nothing written |
| `OutpostNotAdmitted` | a quota is reached, or the call is too large for the door | no: nothing written |
| `OutpostOffline` | no instance of the outpost is there to claim the call, or none did in time | no |
| `OutpostRefused` | the outpost's own policy refused (its code, from a closed list) | no |
| `OutpostTimeout` | a claimed read got no answer in time | unknown; a read may be asked again |
| `OutpostInDoubt` | a claimed write got no answer in time | unknown; never asked again |

They are `AdapterError`s: where a governed action plays its effect, a refusal is final and is not
retried.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from choregos_contracts import OutpostRefusalCode

from ..errors import ConfigurationError


class OutpostRefusal(ConfigurationError):  # noqa: N818 - a typed refusal, named by its code
    """A call routed to an outpost that gave no result. `outpost` is the outpost's name and
    `endpoint` the alias the call was for."""

    code: ClassVar[str] = "outpost_refused"
    #: The outpost's server received nothing: the call is not booked (ADR 0149 §7). False when
    #: nobody knows whether it did.
    reached_no_server: ClassVar[bool] = True

    def __init__(self, message: str, *, outpost: str, endpoint: str) -> None:
        super().__init__(message)
        self.outpost = outpost
        self.endpoint = endpoint


class OutpostPaused(OutpostRefusal):
    """The outpost is paused or revoked, or outposts are switched off: nothing was written."""

    code: ClassVar[str] = "outpost_paused"


class OutpostNotAdmitted(OutpostRefusal):
    """The rendezvous did not take the call: a quota of calls in flight or per minute is reached, or
    the call weighs more than the door carries. `reason` says which; nothing was written."""

    code: ClassVar[str] = "outpost_not_admitted"

    def __init__(self, message: str, *, outpost: str, endpoint: str, reason: str) -> None:
        super().__init__(message, outpost=outpost, endpoint=endpoint)
        self.reason = reason


class OutpostOffline(OutpostRefusal):
    """No instance of the outpost was there to claim the call, or none claimed it in time."""

    code: ClassVar[str] = "outpost_offline"


class OutpostRefused(OutpostRefusal):
    """The outpost did not make the call: `refusal` is its code, which names a class of refusal and
    never the rule that produced it (ADR 0147 §10)."""

    code: ClassVar[str] = "outpost_refused"

    def __init__(self, message: str, *, outpost: str, endpoint: str, refusal: OutpostRefusalCode) -> None:
        super().__init__(message, outpost=outpost, endpoint=endpoint)
        self.refusal = refusal


class OutpostTimeout(OutpostRefusal):
    """An instance claimed the read and no answer came within its lease. Executed or not, nobody
    knows; a read may be asked again."""

    code: ClassVar[str] = "outpost_timeout"
    reached_no_server: ClassVar[bool] = False


class OutpostInDoubt(OutpostRefusal):
    """An instance claimed the write and no answer came within its lease: nobody knows whether the
    outpost's server executed it, and it is never asked again (ADR 0149 §5). It carries what a
    person is shown: the call, the instance that claimed it and when."""

    code: ClassVar[str] = "outpost_in_doubt"
    reached_no_server: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        outpost: str,
        endpoint: str,
        call_id: str,
        tool: str | None = None,
        instance_id: str | None = None,
        claimed_at: datetime | None = None,
    ) -> None:
        super().__init__(message, outpost=outpost, endpoint=endpoint)
        self.call_id = call_id
        self.tool = tool
        self.instance_id = instance_id
        self.claimed_at = claimed_at


__all__ = [
    "OutpostInDoubt",
    "OutpostNotAdmitted",
    "OutpostOffline",
    "OutpostPaused",
    "OutpostRefusal",
    "OutpostRefused",
    "OutpostTimeout",
]
