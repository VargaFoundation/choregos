# SPDX-License-Identifier: Apache-2.0
"""Les tables du greffon sont-elles VRAIMENT isolées par organisation sur PostgreSQL ?

Sur SQLite la RLS n'existe pas : ce test ne tourne que contre un vrai PostgreSQL
(`CHOREGOS_TEST_DATABASE_URL`, posée par la CI). Le schéma est celui de `python -m
choregos_api.migrer` avec le greffon installé — la branche `ontology` comprise — et la session est
celle du rôle applicatif NON superutilisateur, sans quoi la RLS ne s'appliquerait pas.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
APP_ROLE, APP_PASSWORD = "choregos_app", "app"

pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"), reason="CHOREGOS_TEST_DATABASE_URL absent : pas de PostgreSQL"
)

TABLES = ("ontology_versions", "managed_objects")


def _url_app(url: str) -> str:
    scheme, rest = url.split("://", 1)
    _, hote = rest.rsplit("@", 1)
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{hote}"


async def _administrer(*ordres: str) -> Any:
    import asyncpg

    connexion = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        resultat = None
        for ordre in ordres:
            resultat = await connexion.fetch(ordre)
        return resultat
    finally:
        await connexion.close()


@pytest.fixture
async def base_migree(greffon: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    from alembic import command
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _administrer(
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    # `migrations/env.py` fait `asyncio.run()` : dans un fil à part, hors de la boucle du test.
    await asyncio.to_thread(command.upgrade, configuration(), "heads")
    try:
        yield
    finally:
        await db_session.dispose_engine()
        await _administrer("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()


async def test_les_tables_du_greffon_sont_sous_rls_forcee(base_migree: None) -> None:
    lignes = await _administrer(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        f"WHERE relname IN ({', '.join(repr(t) for t in TABLES)})"
    )
    assert {(r["relname"], r["relrowsecurity"], r["relforcerowsecurity"]) for r in lignes} == {
        (table, True, True) for table in TABLES
    }


async def test_une_organisation_ne_voit_ni_les_objets_ni_l_ontologie_de_l_autre(base_migree: None) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject, OntologyVersion
    from sqlalchemy import select

    projets: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            projet = Project(org_id=org.id, slug="infra", name="Infra", status="active", config={})
            session.add(projet)
            await session.flush()
            projets[slug] = projet.id
            session.add(
                OntologyVersion(
                    project_id=projet.id,
                    name="core-ref",
                    version="0.1.0",
                    checksum="sha256:x",
                    compiled_ir={},
                )
            )
            session.add(
                ManagedObject(
                    project_id=projet.id,
                    object_type="finding",
                    id=f"os-{slug}",
                    properties={"key": f"os-{slug}"},
                )
            )

    async with session_scope("a") as session:
        objets = (await session.execute(select(ManagedObject.id))).scalars().all()
        versions = (await session.execute(select(OntologyVersion.project_id))).scalars().all()
    assert objets == ["os-a"]
    assert versions == [projets["a"]]

    async with session_scope() as session:
        assert (await session.execute(select(ManagedObject.id))).scalars().all() == [], "fail-closed"

    # Une session de `a` ne peut pas écrire un objet dans le projet de `b`.
    with pytest.raises(Exception, match="row-level security"):
        async with session_scope("a") as session:
            session.add(
                ManagedObject(project_id=projets["b"], object_type="finding", id="intrus", properties={})
            )


async def test_onto0003_copie_les_propositions_sous_rls_forcee(
    greffon: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La copie de onto0003 tourne sous le rôle de l'application, tables en RLS forcée : sans
    `set_config('app.current_orgs', '*', true)`, elle ne lirait aucune proposition — en silence."""
    import json

    from alembic import command
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _administrer(
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    ir = json.dumps({"action_types": [{"name": "open_infra_pr", "effects": [{}], "evidence": []}]})
    try:
        await asyncio.to_thread(command.upgrade, configuration(), "f8a0b2c4d6e9")
        await asyncio.to_thread(command.upgrade, configuration(), "onto0002")
        from choregos_api.db.models import Organization, Project
        from choregos_api.db.session import session_scope
        from choregos_ontology.service.store import OntologyVersion
        from sqlalchemy import text

        async with session_scope(orgs="*") as session:
            session.add(Organization(id="o1", slug="varga", name="Varga"))
            session.add(Project(id="p1", org_id="o1", slug="infra", name="Infra", status="active", config={}))
            await session.flush()
            session.add(
                OntologyVersion(
                    id="v1",
                    project_id="p1",
                    name="it4it",
                    version="1",
                    checksum="sha",
                    compiled_ir=json.loads(ir),
                )
            )
            await session.flush()
            colonnes = (
                "id, project_id, version_id, action_type, target_ids, params, justification, status, "
                "proposed_by, approval, decisions, effects, evidence"
            )
            valeurs = (
                "'a1', 'p1', 'v1', 'open_infra_pr', '[]', '{}', 'x', 'pending_approval', '{}', '{}', "
                "'[]', '[]', '[]'"
            )
            await session.execute(text(f"INSERT INTO action_proposals ({colonnes}) VALUES ({valeurs})"))
        await db_session.dispose_engine()
        await asyncio.to_thread(command.upgrade, configuration(), "heads")
        lignes = await _administrer("SELECT id, origin, status, effects FROM actions")
        assert [(r["id"], r["origin"], r["status"]) for r in lignes] == [
            ("a1", "ontology", "pending_approval")
        ]
        assert json.loads(lignes[0]["effects"]) == [{"effect": "ontology.effet", "with": {"index": 0}}]
        restantes = await _administrer("SELECT to_regclass('public.action_proposals') AS table")
        assert restantes[0]["table"] is None, "la table est retirée"
    finally:
        await db_session.dispose_engine()
        await _administrer("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()
