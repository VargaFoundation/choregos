"""Les migrations disent-elles la même chose que les modèles ?

Ce test existe à cause d'un défaut réel : `runs.spend_collected` vivait dans le modèle et
dans aucune migration. Rien ne le disait, parce que l'API posait le schéma elle-même au
démarrage (`create_all`) et que le banc sur cluster lançait alembic avec `|| true`. Le
défaut n'apparaissait que sur une vraie base migrée, au premier commentaire d'état écrit.
"""

from __future__ import annotations

import pathlib

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from choregos_api.config import get_settings
from choregos_api.db.models import Base
from sqlalchemy import create_engine

API = pathlib.Path(__file__).resolve().parents[1]


def _config(url: str) -> Config:
    """`migrations/env.py` prend l'URL des RÉGLAGES, pas de la config alembic (c'est ce qui
    évite de répéter la chaîne de connexion en deux endroits). Le test suit donc le même
    chemin : il pose la variable d'environnement et vide le cache des réglages."""
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_les_migrations_produisent_le_schema_des_modeles(tmp_path: pathlib.Path) -> None:
    """Une base migrée doit être indiscernable de `Base.metadata`.

    SQLite suffit : les migrations n'emploient aucun type propre à PostgreSQL. Ce qu'on
    compare, ce sont les tables et les colonnes — pas les détails de dialecte, qui sont
    filtrés plus bas.
    """
    fichier = tmp_path / "migre.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    get_settings.cache_clear()
    try:
        command.upgrade(_config(f"sqlite:///{fichier}"), "head")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    url = f"sqlite:///{fichier}"

    moteur = create_engine(url)
    with moteur.connect() as connexion:
        contexte = MigrationContext.configure(connexion)
        ecarts = compare_metadata(contexte, Base.metadata)

    interessants = [
        difference
        for difference in ecarts
        # `modify_type` et `modify_default` sur SQLite parlent du dialecte, pas du schéma :
        # une colonne absente ou en trop, elle, est un vrai désaccord.
        if not (isinstance(difference, tuple) and str(difference[0]).startswith("modify_"))
    ]
    if interessants:
        detail = "\n".join(f"  - {difference}" for difference in interessants)
        pytest.fail(
            "les migrations et les modèles ne disent pas la même chose :\n"
            f"{detail}\n\n"
            "Générer la migration manquante :\n"
            "  cd apps/api && alembic revision --autogenerate -m '…'"
        )


def test_les_migrations_redescendent_et_remontent(tmp_path: pathlib.Path) -> None:
    """`downgrade base` puis `upgrade head` : aucune migration n'était jamais redescendue."""
    fichier = tmp_path / "aller-retour.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    get_settings.cache_clear()
    try:
        config = _config(f"sqlite:///{fichier}")
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        moteur = create_engine(f"sqlite:///{fichier}")
        with moteur.connect() as connexion:
            tables = connexion.exec_driver_sql("select name from sqlite_master where type='table'").fetchall()
        assert {t[0] for t in tables} <= {"alembic_version"}, "tout doit être redescendu"
        command.upgrade(config, "head")
        with moteur.connect() as connexion:
            ecarts = compare_metadata(MigrationContext.configure(connexion), Base.metadata)
        assert not [d for d in ecarts if not (isinstance(d, tuple) and str(d[0]).startswith("modify_"))]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
