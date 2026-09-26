"""La RLS PostgreSQL isole-t-elle VRAIMENT une organisation d'une autre ?

Jamais exercée avant le 2026-09-24 : toute la suite tournait sur SQLite, où la politique
n'existe pas, et la politique elle-même était fail-open. Ce test ne tourne que contre un
vrai PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`) : la CI en lance un, en local
`docs/development.md` dit comment.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, sans_postgres

pytestmark = sans_postgres


@pytest.fixture
async def deux_organisations(pg_app: Any) -> dict[str, str]:
    """`a` et `b`, chacune avec un projet `billing-api` et un ticket."""
    from choregos_api.db.models import Organization, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            projet = Project(
                org_id=org.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(projet)
            await session.flush()
            await ensure_defaults(session, projet)
            session.add(WorkItem(project_id=projet.id, tracker_key=f"{slug}-1", title="T", state="ready"))
            ids[slug] = projet.id
    return ids


async def test_une_session_sans_organisation_ne_voit_rien(deux_organisations: dict[str, str]) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:  # aucune portée : c'est l'oubli qui ouvrait tout
        assert (await session.execute(select(Project))).scalars().all() == []
        assert (await session.execute(select(WorkItem))).scalars().all() == []
    async with session_scope(orgs=["a"]) as session:
        projets = (await session.execute(select(Project))).scalars().all()
        assert [p.id for p in projets] == [deux_organisations["a"]]
        tickets = (await session.execute(select(WorkItem.tracker_key))).scalars().all()
        assert tickets == ["a-1"]
        # même par identifiant : la ligne de l'autre organisation n'existe pas pour cette session
        assert await session.get(Project, deux_organisations["b"]) is None
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(Project))).scalars().all()) == 2


async def test_par_l_api_un_membre_de_a_ne_voit_pas_b(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """Le principal borne la session : le projet de `b` n'existe pas pour un membre de `a` (404, pas 403)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")  # développeur de `a` seulement (OIDC_DEFAULT_ORG=a)
        me = (await client.get("/api/v1/me")).json()
        assert {m["org"] for m in me["memberships"]} == {"a"}

        assert (await client.get(f"/api/v1/projects/{deux_organisations['a']}")).status_code == 200
        assert (await client.get(f"/api/v1/projects/{deux_organisations['b']}")).status_code == 404
        assert (await client.get("/api/v1/projects/b:billing-api")).status_code == 404
        liste = (await client.get("/api/v1/orgs/a/projects")).json()
        assert [p["slug"] for p in liste["items"]] == ["billing-api"]


async def test_un_administrateur_cree_un_projet_dans_son_organisation(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """L'INSERT passe la politique (jugée sur `org_id`, pas sur un sous-select qui ne voit pas la ligne)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        created = await client.post(
            "/api/v1/orgs/a/projects",
            json={"slug": "nouveau", "name": "Nouveau", "config": {"slug": "nouveau", "org": "a"}},
        )
        assert created.status_code == 201, created.text
        assert (
            await client.post(
                "/api/v1/orgs/b/projects",
                json={"slug": "intrus", "name": "Intrus", "config": {"slug": "intrus", "org": "b"}},
            )
        ).status_code == 403


async def test_un_jeton_de_run_voit_son_run_quelle_que_soit_l_organisation(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope(orgs="*") as session:
        item = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == "b-1"))).scalar_one()
        run = Run(
            id="b-1-t-implement-1",
            work_item_id=item.id,
            project_id=deux_organisations["b"],
            transition_id="t-implement",
            stage_role="implement",
            attempt=1,
            actor="agent",
            backend="claude-code",
            model="platform/standard",
            status="running",
            stage_input={"schema": "choregos/StageInput/v1"},
        )
        session.add(run)
    jeton = mint_run_token(
        "b-1-t-implement-1", project_slug="billing-api", work_item_key="b-1", ttl_minutes=5
    )
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        reponse = await client.get(
            "/api/v1/internal/runs/b-1-t-implement-1/ticket", headers={"Authorization": f"Bearer {jeton}"}
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["key"] == "b-1"


@pytest.fixture
async def trois_traces(deux_organisations: dict[str, str]) -> dict[str, str]:
    """Une trace pour `a`, une pour `b`, une de plateforme (sans organisation)."""
    from choregos_api.db.models import AuditLog, Organization
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        orgs = {org.slug: org.id for org in (await session.execute(select(Organization))).scalars().all()}
        for slug in ("a", "b"):
            trace = AuditLog(
                actor_id=f"u-{slug}",
                actor_kind="user",
                org_id=orgs[slug],
                action="project.update",
                target_type="project",
                target_id=deux_organisations[slug],
                payload={},
                ts=utcnow(),
            )
            session.add(trace)
            await session.flush()
            ids[slug] = trace.id
        plateforme = AuditLog(
            actor_id="u-a",
            actor_kind="user",
            org_id=None,
            action="auth.login",
            target_type="user",
            target_id="u-a",
            payload={},
            ts=utcnow(),
        )
        session.add(plateforme)
        await session.flush()
        ids["plateforme"] = plateforme.id
    return ids


async def test_la_trace_d_audit_d_une_organisation_est_invisible_a_l_autre(
    pg_app: Any, trois_traces: dict[str, str]
) -> None:
    """`audit_log` était la dernière table de mutation hors RLS — et sans colonne d'organisation."""
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope(orgs=["a"]) as session:
        vues = (await session.execute(select(AuditLog.id))).scalars().all()
        assert vues == [trois_traces["a"]], "une session bornée à `a` voit autre chose que `a`"
    async with session_scope() as session:  # aucune portée : l'oubli ne doit rien ouvrir
        assert (await session.execute(select(AuditLog))).scalars().all() == []
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(AuditLog))).scalars().all()) == 3


async def test_par_l_api_membre_des_deux_mais_audit_read_dans_une_seule(
    pg_app: Any, trois_traces: dict[str, str]
) -> None:
    """Le cas que la RLS seule NE couvre PAS, et c'est celui qui fuyait.

    Un principal membre de `a` **et** de `b` a une session bornée aux deux : la politique
    laisse donc passer les traces des deux. Seule la route peut distinguer « membre de `b` »
    de « autorisé à lire l'audit de `b` ». Avant le 2026-09-26 elle ne distinguait rien —
    `GET /audit` renvoyait `select(AuditLog)` sans le moindre `where`, et `audit:read` dans
    une organisation donnait l'audit de toutes les autres.

    Retirer le filtre de `routers/admin.py` fait rougir ce test, et lui seul.
    """
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")  # ORG_ADMIN de `a` — donc `audit:read` sur `a`
        async with session_scope(orgs="*") as session:
            user = (
                await session.execute(select(User).where(User.email == "admin@a.test"))
            ).scalar_one()
            org_b = (
                await session.execute(select(Organization).where(Organization.slug == "b"))
            ).scalar_one()
            # simple lecteur chez `b` : la session le verra, la permission non
            session.add(Membership(user_id=user.id, org_id=org_b.id, project_id=None, role="viewer"))

        me = (await client.get("/api/v1/me")).json()
        assert {m["org"] for m in me["memberships"]} == {"a", "b"}, "le principal doit porter les deux"

        page = await client.get("/api/v1/audit")
        assert page.status_code == 200, page.text
        ids = {ligne["id"] for ligne in page.json()["items"]}
        assert trois_traces["b"] not in ids, "l'audit de `b` fuit vers un simple lecteur de `b`"
        assert trois_traces["plateforme"] not in ids, "un événement de plateforme fuit"
        assert trois_traces["a"] in ids, "l'audit de `a` doit rester lisible par son administrateur"
