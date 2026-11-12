# SPDX-License-Identifier: Apache-2.0
"""A write through an outpost: what its rule must say before it is opened, and how it is delivered
(ADR 0149 §5-6).

The platform cannot see an outpost's server. Once an instance of the outpost has claimed a write, an
answer that never comes leaves one fact unknown: whether the server executed it. So a write is
**delivered at most once**, under an id derived from the key of the governed effect that plays it,
and a claimed write whose answer does not come is **in doubt**: never delivered again, never
compensated automatically.

That is a guarantee weaker than the one a direct connector's retried, idempotent effect gives, and it
is not implied by silence (ADR 0101). The administrator who opens such a write says one of two
things in the operation's own rule:

- `platform_arguments` lists `action_key`: the operation takes the platform's idempotency key. The
  platform fills it, the same at every delivery of one call, and after a doubt the write may be
  delivered again with it: the server, not the platform, makes the second delivery harmless;
- `in_doubt_accepted`: an in-doubt outcome is accepted for the operation, explicitly.

What is here: the rule as `updateConnectorOperation` judges it (`stated`), the same rule as the
effect reads it again before anything leaves (`said`), the key each delivery of a write is placed
under (`Delivery`), and what an in-doubt delivery is shown with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from choregos_adapters.outpost import OutpostInDoubt
from choregos_contracts import arguments_sha256

from ..db.models import ConnectorOperation, OrgConnector
from ..errors import ApiError, unprocessable
from .connectors import declared_operation
from .mcp_gateway import OUTPOST_TYPE

#: The platform argument that is an idempotency key (`effects._platform_arguments`).
IDEMPOTENCY_KEY: Final = "action_key"
#: The two fields of an operation's rule either of which opens a write through an outpost.
STATEMENTS: Final = ("platform_arguments", "in_doubt_accepted")
OPEN: Final = ("allowed", "approval")


def at_an_outpost(connector: OrgConnector) -> bool:
    return connector.kind == "mcp" and connector.type == OUTPOST_TYPE


def is_write(connector: OrgConnector, operation: ConnectorOperation) -> bool:
    """A write through an outpost, as the rendezvous itself judges it (`outpost_route._is_write`):
    everything the operation's row does not call a read."""
    return at_an_outpost(connector) and operation.access != "read"


def platform_arguments_of(
    connector: OrgConnector, operation: str, stated_by_the_rule: Sequence[str] | None
) -> tuple[str, ...]:
    """The arguments the platform fills on a governed call of an operation: what its type or its
    plugin's server declares (ADR 0052, ADR 0096); for a discovered tool, which declares nothing,
    what the organisation's rule states (ADR 0149 §6). A declaration is never widened by a row."""
    declared = declared_operation(connector, operation)
    if declared is not None:
        return tuple(declared.platform_arguments)
    return tuple(stated_by_the_rule or ())


def takes_the_key(connector: OrgConnector, operation: ConnectorOperation) -> bool:
    """The operation takes the platform's idempotency key: its rule lists `action_key`."""
    return IDEMPOTENCY_KEY in platform_arguments_of(connector, operation.name, operation.platform_arguments)


def said(connector: OrgConnector, operation: ConnectorOperation) -> bool:
    """One of the two statements is on the operation's rule."""
    return takes_the_key(connector, operation) or bool(operation.in_doubt_accepted)


def _refused(where: str, field: str, detail: str) -> ApiError:
    return unprocessable(f"{where}: {detail}", [{"loc": ["body", field], "msg": detail}])


def neither_is_said(where: str) -> ApiError:
    """The 422 of a write through an outpost that would be open under no statement: it names both."""
    detail = (
        f"{where} is a write of a connector reached through an outpost. Such a write is delivered at "
        "most once: when the instance that claimed it never answers, nobody knows whether the outpost's "
        "server executed it (ADR 0149 §5-6). It is opened only when its rule says one of two things, "
        "and it says neither: `platform_arguments` lists `action_key` (the operation takes the "
        "platform's idempotency key, and may be delivered again with the same key), or "
        "`in_doubt_accepted` is true (an action that calls it may end `in_doubt`: never replayed, "
        "never compensated automatically). The operation stays as it was"
    )
    said_nowhere = "a write through an outpost says how it is delivered: this, or the other"
    return unprocessable(detail, [{"loc": ["body", field], "msg": said_nowhere} for field in STATEMENTS])


def stated(
    connector: OrgConnector,
    operation: ConnectorOperation,
    *,
    policy: str,
    platform_arguments: list[str] | None,
    in_doubt_accepted: bool,
) -> None:
    """Judge an operation's rule as it would be once a change is applied (ADR 0149 §6, ADR 0101):
    422 for a statement the platform would not act on, and for a write through an outpost left open
    under neither statement. Nothing is written here.

    - `in_doubt_accepted` is a statement about a write through an outpost and about nothing else:
      a direct connector's write is retried, and a read is asked again;
    - `platform_arguments` is said by the rule for a discovered tool only: an operation its type or
      its plugin's server declares takes them from there. It is a write's, and each name is an
      argument the operation's input schema has: a key the server would never read promises nothing.
    """
    where = f"{connector.name}/{operation.name}"
    if in_doubt_accepted and not is_write(connector, operation):
        raise _refused(
            where,
            "in_doubt_accepted",
            "an in-doubt outcome exists for a write of a connector reached through an outpost and for "
            "nothing else: there is nothing to accept here",
        )
    if platform_arguments:
        if declared_operation(connector, operation.name) is not None:
            raise _refused(
                where,
                "platform_arguments",
                "the operation's type declares what the platform fills for it: a rule does not say it "
                "again, nor otherwise",
            )
        if operation.access == "read":
            raise _refused(
                where,
                "platform_arguments",
                "the platform fills its arguments on a governed write: a read takes none",
            )
        has = set((operation.input_schema or {}).get("properties") or {})
        missing = sorted(set(platform_arguments) - has)
        if missing:
            raise _refused(
                where,
                "platform_arguments",
                f"the operation has no argument {', '.join(f'`{name}`' for name in missing)} (its input "
                f"schema declares: {', '.join(sorted(has)) or 'none'}): the platform cannot hand it one",
            )
    takes_key = IDEMPOTENCY_KEY in (platform_arguments or ())
    if policy in OPEN and is_write(connector, operation) and not (takes_key or in_doubt_accepted):
        raise neither_is_said(where)


def state(
    connector: OrgConnector, operation: ConnectorOperation, *, policy: str | None, written: Mapping[str, Any]
) -> None:
    """Apply to an operation's row what a change says of its delivery (`written`: the statements the
    change carries, absent ones left as they are), once the rule it would leave is judged (`stated`).

    The rule is judged as the change would leave it, whenever the change opens the operation or
    touches a statement: withdrawing the last statement of an open write is refused as opening it
    with neither is. A change that only closes, prices or bounds the operation is judged by nothing
    here."""
    if not written and policy not in OPEN:
        return
    platform_arguments = (
        sorted(set(written["platform_arguments"] or []))
        if "platform_arguments" in written
        else list(operation.platform_arguments or [])
    )
    in_doubt_accepted = bool(written.get("in_doubt_accepted", operation.in_doubt_accepted))
    stated(
        connector,
        operation,
        policy=policy or operation.policy,
        platform_arguments=platform_arguments,
        in_doubt_accepted=in_doubt_accepted,
    )
    if "platform_arguments" in written:
        operation.platform_arguments = platform_arguments or None
    if "in_doubt_accepted" in written:
        operation.in_doubt_accepted = in_doubt_accepted


def delivery_key(effect_key: str | None, number: int) -> str | None:
    """The key a delivery of a write is placed once under. The first is the effect's own; one more
    after a doubt, for an operation that takes the idempotency key, is another call of the
    rendezvous: a claimed write is never handed over twice under one id. No key for an effect that
    has none: the route places no write without one."""
    if effect_key is None:
        return None
    return effect_key if number <= 1 else f"{effect_key}:delivery:{number}"


@dataclass(frozen=True, slots=True)
class Delivery:
    """One delivery of a write through an outpost, as the governed effect makes it."""

    #: The key this delivery is placed once under.
    key: str | None
    #: Which delivery of the effect it is: 1, or one more after each doubt of an operation that
    #: takes the idempotency key.
    number: int
    #: After a doubt the write may be delivered again: its rule lists `action_key`.
    again: bool

    @classmethod
    def of(
        cls, connector: OrgConnector, operation: ConnectorOperation, *, effect_key: str | None, doubts: int
    ) -> Delivery:
        """The delivery an attempt of the effect makes, given how many of its deliveries already
        ended in doubt. An operation that does not take the key is always its first delivery: a
        retry meets the first call, whatever became of it, and delivers nothing a second time."""
        again = takes_the_key(connector, operation)
        number = doubts + 1 if again else 1
        return cls(key=delivery_key(effect_key, number), number=number, again=again)


def sent(doubt: OutpostInDoubt, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """What a person is shown of a delivery in doubt (ADR 0149 §5): where it went, which instance
    claimed it and when, and the digest of what it carried. Never the arguments themselves."""
    return {
        "outpost": doubt.outpost,
        "endpoint": doubt.endpoint,
        "operation": doubt.tool or operation,
        "call_id": doubt.call_id,
        "instance_id": doubt.instance_id,
        "claimed_at": doubt.claimed_at.isoformat() if doubt.claimed_at is not None else None,
        "arguments_sha256": arguments_sha256(arguments),
    }


__all__ = [
    "IDEMPOTENCY_KEY",
    "OPEN",
    "STATEMENTS",
    "Delivery",
    "at_an_outpost",
    "delivery_key",
    "is_write",
    "neither_is_said",
    "platform_arguments_of",
    "said",
    "sent",
    "state",
    "stated",
    "takes_the_key",
]
