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


TOUT = ALL_ORGS  # lang: fr-ok alias:internal
limiter_aux_organisations = limit_to_organisations  # lang: fr-ok alias:internal
nommer_l_utilisateur = name_the_user  # lang: fr-ok alias:internal
en_portee_de_plateforme = in_platform_scope  # lang: fr-ok alias:internal
