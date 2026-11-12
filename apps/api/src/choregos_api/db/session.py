# SPDX-License-Identifier: Apache-2.0
"""Async SQLAlchemy session, RLS per organisation, schema creation in dev and test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..logging import get_logger
from .base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
logger = get_logger("choregos.db")

#: What waits for the transaction to commit, then what the commit released (ADR 0085).
_PENDING = "choregos.after_commit.pending"
_READY = "choregos.after_commit.ready"


def after_commit(session: AsyncSession, callback: Callable[[], Awaitable[Any]]) -> None:
    """Run `callback` once this session's transaction has COMMITTED, never if it rolls back (ADR 0085).

    What leaves the database for another system (a Temporal workflow) must not leave before the row
    that justifies it is written: a commit that fails would leave the workflow running on nothing.
    The callback is best effort — a crash between the commit and it loses it —, so what it starts
    must also be found again from the database (`services/action_relay.py`). Register it once the
    transaction has begun (after a write): a rollback of no transaction drops nothing."""
    session.info.setdefault(_PENDING, []).append(callback)


async def run_after_commit(session: AsyncSession) -> None:
    """The callbacks the last commits released, once each. A failure is logged, not raised: the
    transaction is already committed, and the relay starts what the callback could not."""
    ready: list[Callable[[], Awaitable[Any]]] = session.info.pop(_READY, [])
    for callback in ready:
        try:
            await callback()
        except Exception as error:
            logger.warning("after_commit.failed", error=f"{type(error).__name__}: {error}"[:300])


@event.listens_for(Session, "after_commit")
def _released(session: Session) -> None:
    pending = session.info.pop(_PENDING, None)
    if pending:
        session.info.setdefault(_READY, []).extend(pending)


@event.listens_for(Session, "after_rollback")
def _dropped(session: Session) -> None:
    session.info.pop(_PENDING, None)


# ─────────────────── a session that holds no connection during an external call (#1354) ───────────────────

#: Set by a session's owner that lets `outside_the_transaction` cut its transaction.
_RELEASABLE = "choregos.releasable"
#: The transaction-local settings the session's next transaction starts with (`release_connection`).
_RESUME = "choregos.resume_settings"
#: The settings `release_connection` carries to the next transaction: the RLS scope, the user it names
#: and the outpost it names (ADR 0148 §6).
_CARRIED = ("app.current_orgs", "app.current_user", "app.current_outpost")


def allow_release_during_calls(session: AsyncSession) -> None:
    """The owner of `session` lets each `outside_the_transaction` block commit first (#1354).

    Only the owner knows that what its request wrote before an external call need not be atomic
    with what it writes after: the tool route (`callRunTool`) does, since its place under the cap is
    already committed and its line is booked after the call; so do an agent session's response, its
    pod's tool calls and its voice relay's (#1376). A session nobody marked keeps its transaction
    open across the block, as before."""
    session.info[_RELEASABLE] = True


def release_allowed(session: AsyncSession) -> bool:
    """Whether the owner of `session` called `allow_release_during_calls`: then what an external call
    brings back is booked in a transaction of its own, which the code that books it may commit."""
    return bool(session.info.get(_RELEASABLE))


async def release_connection(session: AsyncSession) -> None:
    """Commit `session` and hand its connection back to the pool; its next transaction starts in the
    same scope.

    `set_config(..., true)` lives for one transaction (`limit_to_organisations`, `name_the_user`):
    the scope and the user in force are read before the commit, and the session's next transaction,
    whenever it begins, sets them again before its first statement (`_resumed`). A session that is
    not in a transaction keeps what it was to resume with. The callbacks the commit released run at
    once, as after any commit of a request (ADR 0085): the rows that justify them are written."""
    carried = await _carried_settings(session) if session.in_transaction() else session.info.get(_RESUME)
    await session.commit()
    if carried:
        session.info[_RESUME] = carried
    await run_after_commit(session)


@asynccontextmanager
async def outside_the_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """For the length of the block, an external call (a connector, a provider, a model, a page, a
    search, a governed write's wait), `session` holds no pooled connection, when its owner allowed it
    (`allow_release_during_calls`).

    The request's transaction commits on entry (`release_connection`); nothing is done on exit. A
    statement made inside the block, or after it, begins a new transaction in the same scope. On
    PostgreSQL a transaction held across the call kept a connection `idle in transaction` for as
    long as the call took: a few calls at once filled a replica's pool (#1354)."""
    if session.info.get(_RELEASABLE) and session.in_transaction():
        await release_connection(session)
    yield


async def _carried_settings(session: AsyncSession) -> dict[str, str]:
    if get_settings().is_sqlite:
        return {}
    read = ", ".join(f"current_setting('{name}', true)" for name in _CARRIED)
    values = (await session.execute(text(f"SELECT {read}"))).one()
    return {name: str(value) for name, value in zip(_CARRIED, values, strict=True) if value is not None}


@event.listens_for(Session, "after_begin")
def _resumed(session: Session, _transaction: Any, connection: Any) -> None:
    """The first thing a transaction that follows `release_connection` does: set its scope again."""
    carried: dict[str, str] | None = session.info.pop(_RESUME, None)
    for name, value in (carried or {}).items():
        connection.execute(text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value})


def _create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if not settings.is_sqlite:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_timeout"] = settings.db_pool_timeout_s
        kwargs["pool_recycle"] = settings.db_pool_recycle_s
        kwargs["pool_pre_ping"] = True
    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _create_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


ALL_ORGS = "*"


async def limit_to_organisations(session: AsyncSession, orgs: list[str] | str | None) -> None:
    """Set `app.current_orgs` for PostgreSQL RLS, for THE current transaction only.

    - a list of organisations: the session sees only their projects and what depends on them;
    - `"*"`: the session sees everything, reserved for platform processes (the orchestrator,
      seeds, requests authenticated by a run token);
    - `None` or `[]`: the session sees NOTHING of the tables under RLS. This is the default and it
      is deliberate: the policy used to be `current_org IS NULL OR ...`, open to every session that
      forgot to name itself, and all of them forgot (audit of 2026-09-24).

    Transaction-local (`set_config(..., true)`): nothing survives the commit, so a connection pool
    cannot make the next request inherit an organisation.
    """
    if get_settings().is_sqlite:
        return
    value = ALL_ORGS if orgs == ALL_ORGS else ",".join(sorted(set(orgs))) if orgs else ""
    await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": value})


async def limit_to_remembered_organisations(session: AsyncSession, orgs: list[str]) -> None:
    """Like `limit_to_organisations`, for slugs that were written down EARLIER (a workflow's payload,
    a signed state, a deployment value): a slug that has since been renamed is replaced by the
    organisation's current slug, because RLS compares current slugs (ADR 0090). The aliases are read under
    the platform scope and the narrow scope is set right after, in the same transaction."""
    from .org_aliases import current_slugs

    if get_settings().is_sqlite or not orgs:
        await limit_to_organisations(session, orgs)
        return
    await limit_to_organisations(session, ALL_ORGS)
    resolved = await current_slugs(session, orgs)
    await limit_to_organisations(session, sorted(set(resolved.values())))


async def name_the_user(session: AsyncSession, user_id: str | None) -> None:
    """Set `app.current_user` for the RLS of `users` and `api_tokens`: callers can always read
    themselves, even without a membership. Transaction-local, like the scope."""
    if get_settings().is_sqlite:
        return
    await session.execute(text("SELECT set_config('app.current_user', :u, true)"), {"u": user_id or ""})


async def name_the_outpost(session: AsyncSession, outpost_id: str | None) -> None:
    """Set `app.current_outpost` for the RLS of the outposts' tables (ADR 0148 §6), for THE current
    transaction only.

    The rows that belong to one outpost (its instances, its enrolment tokens, its calls) are visible
    and writable only to a session that names that outpost, inside an organisation scope that holds
    it: the second setting narrows the first, it never widens it. `None` names no outpost, which is
    the default and fails closed: none of those rows. There is no "every outpost" value. A path
    names the outpost it works for: the door, the one its caller's signature resolves to; the
    management routes, the one in their URL; the broker, the one its connector routes to
    (`org_connectors.outpost_id`).
    """
    if get_settings().is_sqlite:
        return
    await session.execute(
        text("SELECT set_config('app.current_outpost', :outpost, true)"), {"outpost": outpost_id or ""}
    )


@asynccontextmanager
async def in_outpost_scope(session: AsyncSession, outpost_id: str) -> AsyncIterator[None]:
    """For the length of a block, the session names this outpost; what it named before is RESTORED.

    For a request that works on several outposts in turn (the organisation's list), or that goes on
    after it is done with one. What the block wrote must be flushed before it ends: an `INSERT` or an
    `UPDATE` sent later would be judged without the outpost named, and refused.
    """
    if get_settings().is_sqlite:
        yield
        return
    before = (await session.execute(text("SELECT current_setting('app.current_outpost', true)"))).scalar_one()
    await name_the_outpost(session, outpost_id)
    try:
        yield
    finally:
        await name_the_outpost(session, before or None)


@asynccontextmanager
async def in_platform_scope(session: AsyncSession) -> AsyncIterator[None]:
    """For the length of a block, the session sees the whole instance; the previous scope is RESTORED.

    For what concerns the entire instance in the middle of a bounded request: counting the
    organisations (who administers the platform?), finding a user by e-mail, unique across the
    instance. Under the identity RLS, a count made from the scope of a single organisation's
    administrator would be 1, and they would pass for the administrator of everything.
    """
    if get_settings().is_sqlite:
        yield
        return
    before = (await session.execute(text("SELECT current_setting('app.current_orgs', true)"))).scalar_one()
    await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": ALL_ORGS})
    try:
        yield
    finally:
        await session.execute(
            text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": before or ""}
        )


@asynccontextmanager
async def session_scope(
    org_slug: str | None = None, *, orgs: list[str] | str | None = None
) -> AsyncIterator[AsyncSession]:
    """A transactional session, limited to the given organisations (see `limit_to_organisations`).

    Without `orgs` or `org_slug`, the session sees no table under RLS on PostgreSQL. A platform
    process passes `orgs="*"` and says so.
    """
    async with get_sessionmaker()() as session:
        scope = orgs if orgs is not None else ([org_slug] if org_slug else None)
        if scope is not None:
            if isinstance(scope, list):
                # `orgs=[...]` comes from a payload, a state or a deployment value as often as from a
                # request: an alias is replaced by the current slug (ADR 0090).
                await limit_to_remembered_organisations(session, scope)
            else:
                await limit_to_organisations(session, scope)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


async def create_all() -> None:
    """Create the schema (dev, tests). In staging and production, Alembic decides."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
