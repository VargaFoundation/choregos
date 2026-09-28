# SPDX-License-Identifier: Apache-2.0
"""`python -m choregos_api.migrer` joue-t-il le cœur de n'importe où, et la branche d'un greffon ?

Le greffon est installé pour de vrai (un `.dist-info` sur disque qui déclare le groupe
`choregos.migrations`) ; c'est `migrer` qui le trouve, comme dans le Job du chart.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text

API_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _tete_du_coeur() -> str:
    from alembic.script import ScriptDirectory
    from choregos_api.migrer import configuration

    (tete,) = ScriptDirectory.from_config(configuration(avec_les_greffons=False)).get_heads()
    return tete


@pytest.fixture
def base(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    fichier = tmp_path / "migree.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    yield f"sqlite:///{fichier}"
    reset_settings_cache()


def _tables(url: str) -> set[str]:
    moteur = create_engine(url)
    try:
        return set(inspect(moteur).get_table_names())
    finally:
        moteur.dispose()


def _versions(url: str) -> set[str]:
    moteur = create_engine(url)
    try:
        with moteur.connect() as connexion:
            return {r[0] for r in connexion.execute(text("SELECT version_num FROM alembic_version"))}
    finally:
        moteur.dispose()


def test_le_coeur_se_migre_depuis_n_importe_quel_repertoire(base: str, tmp_path: pathlib.Path) -> None:
    """L'ancienne commande exigeait `/app/apps/api` comme répertoire de travail."""
    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    fait = subprocess.run(
        [sys.executable, "-m", "choregos_api.migrer"],
        cwd=ailleurs,
        env={**os.environ, "PYTHONPATH": str(API_SRC)},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert fait.returncode == 0, fait.stderr[-1500:]
    assert {"organizations", "projects", "audit_log", "gateway_keys"} <= _tables(base)
    assert _versions(base) == {_tete_du_coeur()}


REVISION_DU_GREFFON = '''
"""Les notes du greffon : SA table, rattachée au cœur par une clé étrangère."""

import sqlalchemy as sa
from alembic import op

revision = "greffon0001"
down_revision = None
branch_labels = ("greffon_schema",)
depends_on = "{tete}"


def upgrade() -> None:
    op.create_table(
        "greffon_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("texte", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("greffon_notes")
'''


def _installer(racine: pathlib.Path, nom: str, source: str, groupe: str, cible: str) -> None:
    (racine / f"{nom}.py").write_text(source, encoding="utf-8")
    info = racine / f"{nom}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {nom}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[{groupe}]\n{nom} = {cible}\n", encoding="utf-8")


@pytest.fixture
def greffon_a_schema(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    racine = tmp_path / "site"
    versions = racine / "greffon_schema_versions"
    versions.mkdir(parents=True)
    (versions / "greffon0001_notes.py").write_text(
        REVISION_DU_GREFFON.replace("{tete}", _tete_du_coeur()), encoding="utf-8"
    )
    module = f"import pathlib\nEMPLACEMENT = pathlib.Path(__file__).parent / {versions.name!r}\n"
    _installer(racine, "greffon_schema", module, "choregos.migrations", "greffon_schema:EMPLACEMENT")
    sys.path.insert(0, str(racine))
    try:
        yield racine
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_schema", None)


def test_la_branche_d_un_greffon_est_jouee_apres_le_coeur(base: str, greffon_a_schema: pathlib.Path) -> None:
    from choregos_api.migrer import main

    main([])

    assert "greffon_notes" in _tables(base)
    assert "organizations" in _tables(base)
    # Une seule ligne, et c'est normal : la branche `depends_on` la tête du cœur, et Alembic ne
    # garde que la révision la plus dépendante — elle couvre l'autre. Le test suivant montre
    # que la tête du cœur revient seule quand la branche redescend.
    assert _versions(base) == {"greffon0001"}


def test_la_branche_du_greffon_redescend_seule(base: str, greffon_a_schema: pathlib.Path) -> None:
    """Désinstaller un greffon doit pouvoir retirer SON schéma sans toucher à celui du cœur."""
    from choregos_api.migrer import main

    main([])
    main(["downgrade", "greffon_schema@base"])

    assert "greffon_notes" not in _tables(base)
    assert _versions(base) == {_tete_du_coeur()}


def test_un_emplacement_declare_mais_absent_arrete_la_commande(tmp_path: pathlib.Path) -> None:
    racine = tmp_path / "site"
    racine.mkdir()
    module = "EMPLACEMENT = '/nulle/part/versions'\n"
    _installer(racine, "greffon_vide", module, "choregos.migrations", "greffon_vide:EMPLACEMENT")
    sys.path.insert(0, str(racine))
    try:
        from choregos_api.migrer import emplacements_des_greffons

        with pytest.raises(RuntimeError, match=r"greffon_vide.*n'existe pas"):
            emplacements_des_greffons()
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_vide", None)
