"""Le pool de connexions est borné, et par la configuration.

`pool_size` seul laisse SQLAlchemy ouvrir dix connexions de débordement de plus par
processus et attendre trente secondes une connexion libre : sous charge, chaque réplique
pouvait tenir vingt connexions et une requête restait suspendue une demi-minute avant de
répondre 500 (état des lieux du 2026-09-24). Le moteur est créé sans se connecter : rien
ici ne demande un PostgreSQL.
"""

from __future__ import annotations

from choregos_api.config import Settings
from choregos_api.db.session import _create_engine


def test_le_pool_postgres_est_borne_par_la_configuration() -> None:
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


def test_les_bornes_par_defaut_sont_des_bornes() -> None:
    """Un débordement fini, une attente courte : une requête sans connexion répond vite
    plutôt que de rester suspendue trente secondes avant un 500."""
    s = Settings(database_url="postgresql+asyncpg://u:p@localhost:1/x")
    assert 0 <= s.db_max_overflow <= s.db_pool_size
    assert s.db_pool_timeout_s <= 10
    assert s.db_pool_recycle_s > 0
