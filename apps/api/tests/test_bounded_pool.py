"""The connection pool is bounded, and by the configuration.

`pool_size` alone lets SQLAlchemy open ten more overflow connections per process and wait thirty
seconds for a free connection: under load, each replica could hold twenty connections and a request
stayed suspended for half a minute before answering 500 (inventory of 2026-09-24). The engine is
created without connecting: nothing here needs a PostgreSQL.
"""

from __future__ import annotations

from choregos_api.config import Settings
from choregos_api.db.session import _create_engine


def test_the_postgres_pool_is_bounded_by_the_configuration() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:1/x",
        db_pool_size=3,
        db_max_overflow=2,
        db_pool_timeout_s=4,
        db_pool_recycle_s=600,
    )
    engine = _create_engine(settings)
    try:
        pool = engine.pool
        assert pool.size() == 3
        assert pool._max_overflow == 2
        assert pool._timeout == 4
        assert pool._recycle == 600
        assert pool._pre_ping is True
    finally:
        engine.sync_engine.dispose()


def test_the_default_bounds_are_bounds() -> None:
    """A finite overflow, a short wait: a request without a connection answers quickly
    rather than staying suspended thirty seconds before a 500."""
    s = Settings(database_url="postgresql+asyncpg://u:p@localhost:1/x")
    assert 0 <= s.db_max_overflow <= s.db_pool_size
    assert s.db_pool_timeout_s <= 10
    assert s.db_pool_recycle_s > 0
