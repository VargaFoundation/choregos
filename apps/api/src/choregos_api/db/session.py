"""Session SQLAlchemy async, RLS par organisation, création du schéma en dev/test."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings, get_settings
from .base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if not settings.is_sqlite:
        kwargs["pool_size"] = settings.db_pool_size
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
            await limiter_aux_organisations(session, portee)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Crée le schéma (dev, tests). En staging et prod, c'est Alembic qui décide."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
