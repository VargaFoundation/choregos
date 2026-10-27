# SPDX-License-Identifier: Apache-2.0
"""Session SQLAlchemy async, RLS par organisation, création du schéma en dev/test."""

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


TOUT = "*"


async def limiter_aux_organisations(session: AsyncSession, orgs: list[str] | str | None) -> None:
    """Pose `app.current_orgs` pour la RLS PostgreSQL, le temps de LA transaction en cours.

    - une liste d'organisations : la session ne voit que leurs projets et ce qui en dépend ;
    - `"*"` : la session voit tout — réservé aux processus de la plateforme (orchestrateur,
      seeds, requêtes authentifiées par un jeton de run) ;
    - `None` ou `[]` : la session ne voit RIEN des tables sous RLS. C'est le défaut, et
      c'est voulu : la politique était `current_org IS NULL OR …`, donc ouverte à toute
      session qui oubliait de se nommer — et toutes oubliaient (état des lieux du
      2026-09-24).

    Transaction-locale (`set_config(…, true)`) : rien ne survit au commit, un pool de
    connexions ne peut pas faire hériter une organisation à la requête suivante.
    """
    if get_settings().is_sqlite:
        return
    valeur = TOUT if orgs == TOUT else ",".join(sorted(set(orgs))) if orgs else ""
    await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": valeur})


async def limit_to_remembered_organisations(session: AsyncSession, orgs: list[str]) -> None:
    """Like `limiter_aux_organisations`, for slugs that were written down EARLIER (a workflow's payload,
    a signed state, a deployment value): a slug that has since been renamed is replaced by the
    organisation's current slug, because RLS compares current slugs (ADR 0090). The aliases are read under
    the platform scope and the narrow scope is set right after, in the same transaction."""
    from .org_aliases import current_slugs

    if get_settings().is_sqlite or not orgs:
        await limiter_aux_organisations(session, orgs)
        return
    await limiter_aux_organisations(session, TOUT)
    resolved = await current_slugs(session, orgs)
    await limiter_aux_organisations(session, sorted(set(resolved.values())))


async def nommer_l_utilisateur(session: AsyncSession, user_id: str | None) -> None:
    """Pose `app.current_user` pour la RLS de `users` et `api_tokens` : l'appelant se lit toujours,
    même sans appartenance. Transaction-locale, comme la portée."""
    if get_settings().is_sqlite:
        return
    await session.execute(text("SELECT set_config('app.current_user', :u, true)"), {"u": user_id or ""})


@asynccontextmanager
async def en_portee_de_plateforme(session: AsyncSession) -> AsyncIterator[None]:
    """Le temps d'un bloc, la session voit toute l'instance ; la portée précédente est RESTAURÉE.

    Pour ce qui porte sur l'instance entière au milieu d'une requête bornée : compter les
    organisations (qui administre la plateforme ?), retrouver un utilisateur par son e-mail, unique à
    l'échelle de l'instance. Sous la RLS de l'identité, un compte fait depuis la portée d'un
    administrateur d'une seule organisation vaudrait 1 — et il passerait pour administrateur de tout.
    """
    if get_settings().is_sqlite:
        yield
        return
    avant = (await session.execute(text("SELECT current_setting('app.current_orgs', true)"))).scalar_one()
    await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": TOUT})
    try:
        yield
    finally:
        await session.execute(
            text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": avant or ""}
        )


@asynccontextmanager
async def session_scope(
    org_slug: str | None = None, *, orgs: list[str] | str | None = None
) -> AsyncIterator[AsyncSession]:
    """Session transactionnelle, limitée aux organisations données (voir `limiter_aux_organisations`).

    Sans `orgs` ni `org_slug`, la session ne voit aucune table sous RLS sur PostgreSQL. Un
    processus de la plateforme passe `orgs="*"` et le dit.
    """
    async with get_sessionmaker()() as session:
        portee = orgs if orgs is not None else ([org_slug] if org_slug else None)
        if portee is not None:
            if isinstance(portee, list):
                # `orgs=[…]` comes from a payload, a state or a deployment value as often as from a
                # request: an alias is replaced by the current slug (ADR 0090).
                await limit_to_remembered_organisations(session, portee)
            else:
                await limiter_aux_organisations(session, portee)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


async def create_all() -> None:
    """Crée le schéma (dev, tests). En staging et prod, c'est Alembic qui décide."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
