# SPDX-License-Identifier: Apache-2.0
"""A connector reached through an outpost: the platform's half of the route (ADR 0149).

An `mcp-outpost` connector names an outpost of its organisation and an endpoint alias: no URL, no
secret. Everything that governs a call is decided before it leaves, where it is decided for a direct
connector (the broker, the governed-write effect, discovery). Only the transport of the outbound
segment changes: instead of an HTTP call to an MCP server, a row an instance of the outpost claims
at its next poll (`outposts.rendezvous`), sealed to that instance and signed by the door.

What is here:

- `route`: whether the outpost a connector names may be handed anything, judged before a call
  leaves and before an action exists (`OutpostPaused` otherwise);
- `declared_outpost`: the same, when a connector is declared or edited, as HTTP answers;
- `DatabaseRendezvous`: the rendezvous port of `choregos_adapters.outpost`, over the database. It
  places in the caller's transaction and commits, waits outside any transaction, and empties what
  is left of a read in the transaction that books it;
- `tool_client`: the factory the platform registers for the type (`adapter_wiring`), and `client`,
  what the one seam of connector calls (`mcp_gateway.connector_client`) builds with it;
- `tool_error` and `problem`: what each typed refusal is for whoever called a tool, and for
  whoever declares or discovers a connector;
- `forgetting_write` and `claimed_write`: what the governed effect that plays a write needs of the
  rendezvous beside the call itself (S65-14): emptying the answer once its result is recorded, and
  learning that an earlier attempt's delivery was claimed.

This module calls the rendezvous; the door's package never imports it (ADR 0148 §12).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

import choregos_adapters
from choregos_adapters import build, fakes_enabled
from choregos_adapters.outpost import (
    OutpostNotAdmitted,
    OutpostOffline,
    OutpostPaused,
    OutpostRefusal,
    OutpostRefused,
    OutpostToolClient,
    client_of,
    port,
)
from choregos_contracts import OutpostCallKind, OutpostCallState, OutpostCapability, OutpostStatus
from choregos_contracts.outpost import OutpostAssertedNames
from choregos_core import utcnow
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    ConnectorOperation,
    NetworkDecision,
    Organization,
    OrgConnector,
    Outpost,
    OutpostCall,
    Project,
)
from ..db.session import in_outpost_scope, release_connection
from ..errors import ApiError, conflict, unprocessable
from ..outposts import rendezvous
from ..secret_store import SecretStoreUnavailable, active_key_wrapper
from . import outposts as registry
from .mcp_gateway_identity import CallIdentity

#: The network journal's layer for a call the platform makes for an agent (ADR 0070).
LAYER: Final = "broker"
#: What a call through an outpost is journaled under, where a host would be (ADR 0149 §11).
JOURNAL_HOST: Final = "outpost:{outpost}/{endpoint}"
#: How long a caller is told to wait before it asks again, when a quota refused its call.
RETRY_AFTER_S: Final = rendezvous.RATE_WINDOW_S
#: The types of a tool error that say "nothing left": what every booking of a tool call skips.
NEVER_LEFT: Final = frozenset({"network_policy", "rate_limited"})


# ───────────────────────── the route, judged before anything leaves ─────────────────────────


@dataclass(frozen=True, slots=True)
class OutpostRoute:
    """Where a connector's calls go: an outpost and an alias, and no host (ADR 0149 §3)."""

    outpost: Outpost
    endpoint: str

    @property
    def name(self) -> str:
        return self.outpost.slug


def _switched_off(connector: str, outpost: str) -> str:
    return (
        f"`{connector}` is reached through the outpost `{outpost}`, and outposts are switched off on this "
        "deployment (`CHOREGOS_OUTPOSTS`): nothing is handed to one"
    )


def _not_handed(outpost: Outpost) -> str:
    resumed = (
        "an administrator resumes it" if outpost.status == OutpostStatus.PAUSED.value else "that is final"
    )
    return f"the outpost `{outpost.slug}` is {outpost.status}: it is handed nothing ({resumed})"


async def _outpost_of(session: AsyncSession, instance: OrgConnector) -> Outpost | None:
    """The outpost a connector references, read again: a pause or a revocation is a commit away.
    The registry shows every outpost of the organisation to a session that names none."""
    if not instance.outpost_id:
        return None
    async with in_outpost_scope(session, None):
        found: Outpost | None = (
            await session.execute(
                select(Outpost)
                .where(Outpost.id == instance.outpost_id, Outpost.org_id == instance.org_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return found


async def route(session: AsyncSession, instance: OrgConnector) -> OutpostRoute:
    """How an `mcp-outpost` connector is reached, decided BEFORE anything leaves: `OutpostPaused`
    when outposts are switched off or the outpost is paused or revoked (ADR 0149 §7). Whether an
    instance is there to claim a call is not judged here: an approval may be asked while an outpost
    is offline, and the call itself says so when it is made."""
    config = instance.config or {}
    named, endpoint = str(config.get("outpost") or ""), str(config.get("endpoint") or "")
    if not get_settings().outposts:
        raise OutpostPaused(_switched_off(instance.name, named), outpost=named, endpoint=endpoint)
    outpost = await _outpost_of(session, instance)
    if outpost is None:
        raise OutpostPaused(
            f"`{instance.name}` names the outpost `{named}`, which its organisation does not have: an "
            "administrator routes the connector to one of its outposts",
            outpost=named,
            endpoint=endpoint,
        )
    if outpost.status != OutpostStatus.ACTIVE.value:
        raise OutpostPaused(_not_handed(outpost), outpost=outpost.slug, endpoint=endpoint)
    return OutpostRoute(outpost, endpoint)


async def declared_outpost(
    session: AsyncSession, organisation: Organization, config: Mapping[str, Any], connector: str
) -> Outpost:
    """The outpost a declaration, or an edit, routes a connector to (ADR 0149 §1): an outpost of the
    same organisation, which `org_connectors.outpost_id` then references.

    422 when the configuration names no outpost of the organisation, or one that is not enabled for
    tools. 409 `outpost_paused` when outposts are switched off or the outpost is paused or revoked:
    the refusal a call would meet, said when the connector is declared (ADR 0149 §7). An instance
    need not be there yet: an administrator may declare before the outpost's operator has one
    running, and discovery then says `outpost_offline`."""
    named, endpoint = str(config.get("outpost") or ""), str(config.get("endpoint") or "")
    if not get_settings().outposts:
        refused = OutpostPaused(_switched_off(connector, named), outpost=named, endpoint=endpoint)
        raise problem(refused)
    async with in_outpost_scope(session, None):
        outpost = (
            await session.execute(
                select(Outpost)
                .where(Outpost.org_id == organisation.id, Outpost.slug == named)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    if outpost is None:
        detail = f"`{named}` is not an outpost of {organisation.slug}: an administrator creates it first"
        raise unprocessable(detail, [{"loc": ["body", "config", "outpost"], "msg": detail}])
    if OutpostCapability.TOOLS.value not in (outpost.capabilities or []):
        detail = f"the outpost `{named}` is not enabled for tools: it serves no connector"
        raise unprocessable(detail, [{"loc": ["body", "config", "outpost"], "msg": detail}])
    if outpost.status != OutpostStatus.ACTIVE.value:
        raise problem(OutpostPaused(_not_handed(outpost), outpost=outpost.slug, endpoint=endpoint))
    return outpost


# ───────────────────────── the rendezvous, over the database ─────────────────────────


@dataclass(frozen=True, slots=True)
class Placing:
    """What the platform supplies to reach an outpost for one call: the caller's session, the
    outpost and the connector that routes to it, who the call is made for, and, for a write, the key
    it is placed once under (its effect's, ADR 0149 §5)."""

    session: AsyncSession
    outpost: Outpost
    connector: OrgConnector
    identity: CallIdentity
    effect_key: str | None = None


async def _is_write(session: AsyncSession, connector: OrgConnector, call: port.Call) -> bool:
    """Whether the platform takes the call for a write: read from the operation's own row, never
    from the caller. A tool the connector has no row for is taken for one, the prudent default a
    discovered tool is born with (ADR 0034). A listing writes nothing."""
    if call.kind is not OutpostCallKind.CALL_TOOL:
        return False
    access = (
        await session.execute(
            select(ConnectorOperation.access).where(
                ConnectorOperation.connector_id == connector.id, ConnectorOperation.name == call.tool
            )
        )
    ).scalar_one_or_none()
    return access != "read"


def write_call_id(connector: OrgConnector, effect_key: str) -> str:
    """The id a write is placed once under: derived from the connector and the key of the governed
    effect that plays it (ADR 0149 §5). The same effect, retried, is the same call; whoever makes
    its result durable empties the row under the same id (`rendezvous.forget`)."""
    return rendezvous.call_id_of(connector.id, effect_key)


def forgetting_write(connector: OrgConnector, write_key: str) -> Callable[[AsyncSession], Awaitable[None]]:
    """What empties the rendezvous of a write once whoever played it has made its outcome durable
    (ADR 0148 §11, ADR 0149 §5): the sealed answer and the private half of its answer key.

    The returned function writes in the session it is given and does not commit: called in the
    transaction that records the outcome, the answer goes when that record is kept, and stays for a
    retry to read if the record is lost. Until then a retry of the same effect meets the first call
    and reads its answer; afterwards it never comes back here, since its own record answers it.

    Everything it needs of the connector is read now: the session the connector was read in may be
    over, and rolled back, by the time the outcome is recorded. The in-memory rendezvous of
    `CHOREGOS_FAKES` has no row to empty."""
    outpost_id, call_id, in_memory = connector.outpost_id, write_call_id(connector, write_key), faked()

    async def forget(session: AsyncSession) -> None:
        if not in_memory and outpost_id:
            await rendezvous.forget(session, outpost_id, call_id)

    return forget


#: A write an instance claimed, whatever became of it since: it may have reached the outpost's server.
_MAY_HAVE_BEEN_MADE: Final = frozenset(
    {OutpostCallState.CLAIMED.value, OutpostCallState.ANSWERED.value, OutpostCallState.IN_DOUBT.value}
)


async def claimed_write(
    session: AsyncSession, connector: OrgConnector, write_key: str
) -> dict[str, Any] | None:
    """What the rendezvous holds of a write placed under this key that an instance claimed: where it
    went, who claimed it and when, the digest of what it carried. `None` when there is none: never
    placed, placed and claimed by nobody, or refused by the outpost's own policy, so that it reached
    no server.

    It places nothing and asks nothing of the outpost's state: a paused or revoked outpost is handed
    nothing more, and what an instance of it already took is still on its row. It is how the effect
    that plays a write again learns, when the retry is refused before it leaves, that the attempt
    before it may have been made (ADR 0149 §5)."""
    if faked() or not connector.outpost_id:
        return None
    async with in_outpost_scope(session, connector.outpost_id):
        call = (
            await session.execute(
                select(OutpostCall)
                .where(
                    OutpostCall.outpost_id == connector.outpost_id,
                    OutpostCall.id == write_call_id(connector, write_key),
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    if call is None or call.state not in _MAY_HAVE_BEEN_MADE:
        return None
    claimed_at = call.claimed_at
    if claimed_at is not None and claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=UTC)  # SQLite returns naive datetimes
    return {
        "outpost": str((connector.config or {}).get("outpost") or ""),
        "endpoint": call.endpoint,
        "operation": call.tool,
        "call_id": call.id,
        "instance_id": call.instance_id,
        "claimed_at": claimed_at.isoformat() if claimed_at is not None else None,
        "arguments_sha256": call.arguments_sha256,
    }


def _keyless_write(connector: OrgConnector, call: port.Call) -> port.NotAdmitted:
    return port.NotAdmitted(
        port.WRITE_WITHOUT_KEY,
        f"`{connector.name}/{call.tool}` is a write, and a write through an outpost is delivered at most "
        "once, under the key of the governed effect that plays it (ADR 0149 §5): it is not placed "
        "without one",
    )


#: What the network journal says of each way a call ends (ADR 0149 §11): the decision, and a reason
#: that is a code, never a rule.
_JOURNALED: Final = {
    OutpostCallState.ANSWERED: ("allowed", ""),
    OutpostCallState.EXPIRED: ("unreachable", OutpostOffline.code),
    OutpostCallState.TIMED_OUT: ("unreachable", "outpost_timeout"),
    OutpostCallState.IN_DOUBT: ("unreachable", "outpost_in_doubt"),
}


class DatabaseRendezvous:
    """The rendezvous of one call, over `outpost_calls` (the port of `choregos_adapters.outpost`).

    `place` writes in the caller's transaction and commits it, whoever called: the row exists, and
    the outpost's polls are woken, from that commit on (ADR 0085), and the lock that serialises an
    organisation's placements ends with it. `wait` holds no transaction and no pooled connection:
    each look is a short transaction of its own. `forget` writes in the caller's next transaction
    and does not commit: what is left of the call goes when the caller's own record of it is kept,
    and stays for the sweep if that record is lost (ADR 0148 §11). `cancel` is a transaction of its
    own: whoever gives up may have no session left to give up in.

    Whether a call is a write, and the id it is placed under, are decided here from the platform's
    own rows: a read takes a new id every time, a write the id its effect's key gives, and a write
    that carries no key is not placed."""

    def __init__(self, placing: Placing) -> None:
        self._session = placing.session
        self._outpost = placing.outpost
        self._connector = placing.connector
        self._identity = placing.identity
        self._endpoint = str((placing.connector.config or {}).get("endpoint") or "")
        self._project: Project | None = None
        self._project_read = False

    @property
    def outpost(self) -> str:
        return self._outpost.slug

    async def place(self, call: port.Call) -> str:
        session, outpost = self._session, self._outpost
        self._endpoint = call.endpoint
        try:
            write = await _is_write(session, self._connector, call)
            if write and call.write_key is None:
                raise _keyless_write(self._connector, call)
            call_id = (
                write_call_id(self._connector, str(call.write_key)) if write else rendezvous.new_call_id()
            )
            await self._someone_is_there(call, call_id, write)
            project = await self._project_of_the_call()
            placed = await rendezvous.place(
                session,
                outpost,
                call_id=call_id,
                kind=call.kind,
                endpoint=call.endpoint,
                timeout_s=call.timeout_s,
                write=write,
                tool=call.tool,
                arguments=call.arguments,
                asserted=dict(call.asserted),
                connector_id=self._connector.id,
                project_id=project.id if project is not None else None,
                run_id=self._identity.run,
                action_id=self._identity.action,
            )
        except rendezvous.NotAdmitted as refused:
            raise await self._not_admitted(port.NotAdmitted(refused.reason, str(refused))) from refused
        except port.NotAdmitted as refused:
            raise await self._not_admitted(refused) from refused
        except SecretStoreUnavailable as lack:
            detail = (
                f"outposts are switched on and this deployment cannot seal what a call carries: {lack}. "
                "Nothing is handed to an outpost"
            )
            raise await self._not_admitted(port.NotAdmitted(port.NO_SECRETS_KEY, detail)) from lack
        except ValueError as wrong:
            # Not a call the door carries (a row edited behind the API: a timeout beyond the longest
            # lease, an alias that is none): refused like any other, never raised at the caller.
            detail = f"the call is not one an outpost can be handed: {wrong}"
            raise await self._not_admitted(port.NotAdmitted(port.INVALID_CALL, detail)) from wrong
        await release_connection(session)
        return placed.id

    async def _someone_is_there(self, call: port.Call, call_id: str, write: bool) -> None:
        """Fail fast, and write nothing, when no instance that serves the alias polled within the
        presence window: the call would wait its whole time for a claim that does not come. A write
        that was already placed is not judged again: what became of it is on its row."""
        session, outpost = self._session, self._outpost
        known = await rendezvous.read(session, outpost.id, call_id) if write else None
        if known is not None and known.state is not OutpostCallState.EXPIRED:
            return
        there = await registry.presence(session, outpost)
        if there.serves(call.endpoint):
            return
        missing = f"no instance of it that serves `{call.endpoint}`" if there.online else "no instance of it"
        raise port.NotAdmitted(
            port.OFFLINE,
            f"the outpost `{outpost.slug}` is offline: {missing} polled in the last "
            f"{registry.POLL_WINDOW_S} seconds, and nothing reached its server",
        )

    async def _not_admitted(self, refused: port.NotAdmitted) -> port.NotAdmitted:
        """The refusal, journaled, and the caller's transaction committed: nothing of the call was
        written, and the lock an admission is judged under ends here, not with the caller's request.
        The journal's reason is the refusal's code, with what the rendezvous said after it."""
        if refused.reason == port.OFFLINE:
            decision, reason = "unreachable", OutpostOffline.code
        elif refused.reason in port.CLOSED:
            decision, reason = "denied", f"{OutpostPaused.code}:{refused.reason}"
        else:
            decision = "rate_limited" if refused.reason in port.BUSY else "denied"
            reason = f"{OutpostNotAdmitted.code}:{refused.reason}"
        await self._journal(decision, reason)
        await release_connection(self._session)
        return refused

    async def wait(self, call_id: str) -> port.Outcome | None:
        outcome = await rendezvous.wait(self._outpost.id, call_id)
        if outcome is None:
            return None
        if outcome.state is OutpostCallState.REFUSED:
            code = outcome.refusal.value if outcome.refusal is not None else "refused"
            await self._journal("denied", f"{OutpostRefused.code}:{code}")
        elif outcome.state in _JOURNALED:
            await self._journal(*_JOURNALED[outcome.state])
        return port.Outcome(
            state=outcome.state,
            write=outcome.write,
            answer=outcome.answer,
            refusal=outcome.refusal,
            instance_id=outcome.instance_id,
            claimed_at=outcome.claimed_at,
        )

    async def cancel(self, call_id: str) -> None:
        async with rendezvous.in_scope(self._outpost.id) as scope:
            if scope is not None:
                await rendezvous.cancel(scope.session, self._outpost.id, call_id)

    async def forget(self, call_id: str) -> None:
        await rendezvous.forget(self._session, self._outpost.id, call_id)

    async def _project_of_the_call(self) -> Project | None:
        """The project the call is made for, when it is made for one: a discovery is not."""
        if not self._project_read:
            self._project_read = True
            slug = self._identity.project
            if slug:
                self._project = (
                    await self._session.execute(
                        select(Project).where(Project.org_id == self._connector.org_id, Project.slug == slug)
                    )
                ).scalar_one_or_none()
        return self._project

    async def _journal(self, decision: str, reason: str) -> None:
        """One line of the network journal for this call, under the outpost and the alias where a
        host would be (ADR 0149 §11): the platform opened no connection, and the journal still shows
        that the tool was reached, and where. Written in the caller's transaction, like the line of
        a host the organisation's policy refused. A call that belongs to no project (a discovery)
        leaves none: the audit log carries it.

        The line is counted up first and written only when there is none: a call is journaled every
        time, so two calls of one run to one alias meet on the same line, and the one that loses the
        race counts itself on the line the other wrote."""
        project = await self._project_of_the_call()
        if project is None:
            return
        session, run_id, now = self._session, self._identity.run, utcnow()
        host = JOURNAL_HOST.format(outpost=self._outpost.slug, endpoint=self._endpoint)
        line = NetworkDecision
        counted = (
            update(line)
            .where(
                line.run_id == run_id if run_id else line.run_id.is_(None),
                line.project_id == project.id,
                line.layer == LAYER,
                line.host == host,
                line.port == 0,
                line.decision == decision,
                line.reason == reason,
            )
            .values(count=line.count + 1, last_at=now)
            .execution_options(synchronize_session=False)
        )
        if (await session.execute(counted)).rowcount:  # type: ignore[attr-defined]
            return
        try:
            async with session.begin_nested():
                session.add(
                    line(
                        id=str(uuid.uuid4()),
                        org_id=project.org_id,
                        project_id=project.id,
                        run_id=run_id,
                        agent_slug=(self._identity.agent or "").split("@", 1)[0] or None,
                        layer=LAYER,
                        host=host,
                        port=0,
                        decision=decision,
                        reason=reason,
                        count=1,
                        first_at=now,
                        last_at=now,
                    )
                )
                await session.flush()
        except IntegrityError:
            await session.execute(counted)


class _FakeRendezvous:
    """The process's `FakeOutpost`, standing for the outpost a connector names (`CHOREGOS_FAKES=1`
    on a deployment that has no key to seal a call under): in memory, no row, the same refusals. The
    platform still decides alone that a write carries its key."""

    def __init__(self, placing: Placing, fake: Any) -> None:
        self._placing = placing
        self._fake = fake

    @property
    def outpost(self) -> str:
        return self._placing.outpost.slug

    async def place(self, call: port.Call) -> str:
        placing = self._placing
        if call.write_key is None and await _is_write(placing.session, placing.connector, call):
            raise _keyless_write(placing.connector, call)
        placed: str = await self._fake.place(call)
        return placed

    async def wait(self, call_id: str) -> port.Outcome | None:
        outcome: port.Outcome | None = await self._fake.wait(call_id)
        return outcome

    async def cancel(self, call_id: str) -> None:
        await self._fake.cancel(call_id)

    async def forget(self, call_id: str) -> None:
        await self._fake.forget(call_id)


def faked() -> bool:
    """Whether a connector at an outpost is served by the in-memory `FakeOutpost`: under
    `CHOREGOS_FAKES` and without a secrets key, the rule of the secret store itself
    (`get_secret_store`). With a key the rendezvous is the real one, fakes or not: a call's arguments
    can wait sealed, and whatever polls the door answers it."""
    return fakes_enabled() and active_key_wrapper() is None


def rendezvous_of(placing: Placing) -> port.Rendezvous:
    if faked():
        return _FakeRendezvous(placing, choregos_adapters.fake_outpost())
    return DatabaseRendezvous(placing)


# ───────────────────────── the client the seam builds ─────────────────────────


def _asserted_bounds() -> tuple[int, int]:
    """How long a name the door's contract carries, and how many approvers: read from the contract's
    own model, so that the two cannot disagree."""
    described = OutpostAssertedNames.model_json_schema()["properties"]
    longest = next(part["maxLength"] for part in described["sub"]["anyOf"] if "maxLength" in part)
    return int(longest), int(described["approved_by"]["maxItems"])


_ASSERTED_BOUNDS: Final = _asserted_bounds()


def asserted_names(identity: CallIdentity) -> dict[str, Any]:
    """Who the call is made for, as the outpost may log it: the names the gateway route signs into
    an assertion (ADR 0105), minus the server and the tool (ADR 0149 §3): the server's name is the
    alias the outpost already has, and the tool is the call's own. The names are the contract's
    (`OutpostAssertedNames`), no other. Assertions of the control plane: an outpost decides nothing
    on them.

    Cut to what the door's contract carries (`OutpostAssertedNames`: a name's length, how many
    approvers): a name is what an outpost logs, and one that is too long to log is no reason to
    refuse the call it comes with."""
    fields = OutpostAssertedNames.model_fields
    longest, most = _ASSERTED_BOUNDS
    names: dict[str, Any] = {}
    for name, value in identity.claims().items():
        if name not in fields:
            continue
        if isinstance(value, list):
            names[name] = [str(one)[:longest] for one in value[:most]]
        else:
            names[name] = str(value)[:longest]
    return names


def tool_client(config: dict[str, Any]) -> OutpostToolClient:
    """The factory of the type `mcp-outpost`, as the platform registers it (`adapter_wiring`): the
    tool client of `choregos_adapters.outpost`, over the rendezvous only the platform has. A
    configuration that carries no `placing` was not built by the seam, and builds nothing."""
    placing = config.get("placing")
    if config.get("route") != "outpost" or not isinstance(placing, Placing):
        from choregos_adapters.errors import AdapterError

        raise AdapterError(
            "an `mcp-outpost` connector is reached through its outpost's rendezvous, which only the "
            "platform's connector route supplies: it cannot be built from its configuration alone"
        )
    carried = {
        **config,
        "asserted": asserted_names(placing.identity),
        "write_key": placing.effect_key,
    }
    return client_of(carried, rendezvous_of(placing))


async def client(
    session: AsyncSession,
    instance: OrgConnector,
    identity: Callable[[], Awaitable[CallIdentity]],
    *,
    effect_key: str | None = None,
) -> Any:
    """The client an `mcp-outpost` connector is called with: `route` decides first, and raises."""
    reached = await route(session, instance)
    placing = Placing(session, reached.outpost, instance, await identity(), effect_key)
    return build(
        instance.kind, instance.type, {**(instance.config or {}), "route": "outpost", "placing": placing}
    )


# ───────────────────────── what a refusal is for each caller ─────────────────────────


def tool_error(refused: OutpostRefusal) -> tuple[int, dict[str, Any]]:
    """What a refusal is for whoever called a tool: a status and a body that names the outpost.

    A refusal that reached no server carries a `type` every booking of a tool call already skips
    (`network_policy`, or `rate_limited` for a quota): no ledger line, no price, no count against a
    per-host rate (ADR 0149 §7). A claim that got no answer carries none: the call may have reached
    the outpost's server, and it is booked like any call that left."""
    body: dict[str, Any] = {
        "error": str(refused),
        "code": refused.code,
        "outpost": refused.outpost,
        "endpoint": refused.endpoint,
    }
    if isinstance(refused, OutpostPaused):
        return 403, body | {"type": "network_policy"}
    if isinstance(refused, OutpostOffline):
        return 503, body | {"type": "network_policy"}
    if isinstance(refused, OutpostRefused):
        return 422, body | {"type": "network_policy", "refusal": refused.refusal.value}
    if isinstance(refused, OutpostNotAdmitted):
        if refused.reason in port.BUSY:
            return 429, body | {"type": "rate_limited", "reason": refused.reason}
        status = 413 if refused.reason == port.TOO_LARGE else 422
        return status, body | {"type": "network_policy", "reason": refused.reason}
    return 504, body


def problem(refused: OutpostRefusal) -> ApiError:
    """What a refusal is where a connector is declared, edited or discovered: the contract's
    `OutpostConflict` (409) with its code, the outpost and the alias; a quota is a 429 that says when
    to come back, and a call the rendezvous cannot carry at all a 422."""
    if isinstance(refused, OutpostNotAdmitted):
        if refused.reason in port.BUSY:
            return ApiError(429, "Too many calls to an outpost", str(refused), retry_after=RETRY_AFTER_S)
        return unprocessable(str(refused))
    extra: dict[str, Any] = {"code": refused.code, "outpost": refused.outpost, "endpoint": refused.endpoint}
    if isinstance(refused, OutpostRefused):
        extra["refusal"] = refused.refusal.value
    return conflict(str(refused), extra=extra)


__all__ = [
    "JOURNAL_HOST",
    "NEVER_LEFT",
    "DatabaseRendezvous",
    "OutpostRoute",
    "Placing",
    "asserted_names",
    "claimed_write",
    "client",
    "declared_outpost",
    "faked",
    "forgetting_write",
    "problem",
    "rendezvous_of",
    "route",
    "tool_client",
    "tool_error",
    "write_call_id",
]
