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
        ).status_code in {403, 404}  # 404 sous la RLS de l'identité : `b` est invisible


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
            user = (await session.execute(select(User).where(User.email == "admin@a.test"))).scalar_one()
            org_b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
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


# ─────────── ce qui se rattache à un projet : événements de run, déploiements, clés ───────────


@pytest.fixture
async def rattachees(deux_organisations: dict[str, str]) -> None:
    """Pour chaque organisation : un run et son événement, une release et son déploiement, une clé."""
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import Deployment, GatewayKeyRow, Release, Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug, projet in deux_organisations.items():
            (ticket,) = (
                await session.execute(select(WorkItem).where(WorkItem.project_id == projet))
            ).scalars()
            session.add(
                Run(id=f"run-{slug}", work_item_id=ticket.id, project_id=projet, stage_role="implement")
            )
            release = Release(project_id=projet, env="prod")
            session.add(release)
            await session.flush()
            session.add(RunEvent(run_id=f"run-{slug}", seq=1, type="agent/message", payload={}, ts=utcnow()))
            session.add(Deployment(release_id=release.id, env="prod", status="succeeded"))
            session.add(GatewayKeyRow(key_id=f"cle-{slug}", run_id=f"run-{slug}", project_id=projet))


async def test_les_tables_rattachees_ne_montrent_que_l_organisation_de_la_session(rattachees: None) -> None:
    from choregos_api.db.models import Deployment, GatewayKeyRow, Release, RunEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(RunEvent.run_id))).scalars().all() == ["run-a"]
        assert (await session.execute(select(GatewayKeyRow.key_id))).scalars().all() == ["cle-a"]
        deploiements = (
            (
                await session.execute(
                    select(Release.project_id).join(Deployment, Deployment.release_id == Release.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(deploiements) == 1
        assert len((await session.execute(select(Deployment))).scalars().all()) == 1
    async with session_scope() as session:
        for modele in (RunEvent, Deployment, GatewayKeyRow):
            assert (await session.execute(select(modele))).scalars().all() == [], modele.__tablename__
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(RunEvent))).scalars().all()) == 2


async def test_une_session_de_a_ne_peut_pas_ecrire_un_evenement_sur_le_run_de_b(rattachees: None) -> None:
    """`USING` sert de `WITH CHECK` à l'INSERT : on n'attribue pas une ligne à l'autre organisation."""
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="row-level security"):
        async with session_scope(orgs=["a"]) as session:
            session.add(RunEvent(run_id="run-b", seq=2, type="agent/message", payload={}, ts=utcnow()))


#: Les tables HORS RLS, et pourquoi. Toute autre table doit être sous RLS forcée : une table
#: ajoutée demain sans politique fait rougir `test_chaque_table_est_sous_rls_ou_exemptee`.
EXEMPTEES = {
    # Catalogues de l'instance, en lecture ouverte par décision (`test_routeurs_nus.py`) :
    # aucune donnée d'organisation.
    "agent_backends": "catalogue de l'instance",
    "executors": "catalogue de l'instance",
    "templates": "catalogue de l'instance",
    "model_profiles": "catalogue de l'instance (seules des lignes `scope=platform` sont écrites)",
    "webhook_deliveries": "empreintes de déduplication, sans contenu ni organisation",
    "alembic_version": "table d'Alembic",
}


async def test_chaque_table_est_sous_rls_ou_exemptee(pg_app: Any) -> None:
    from choregos_api.db.session import session_scope
    from sqlalchemy import text

    async with session_scope(orgs="*") as session:
        lignes = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace"
                )
            )
        ).all()
    tables = {nom: (rls, forcee) for nom, rls, forcee in lignes}
    assert len(tables) >= 25, f"{len(tables)} tables seulement : les migrations ont-elles tourné ?"
    sans = sorted(
        nom for nom, (rls, forcee) in tables.items() if not (rls and forcee) and nom not in EXEMPTEES
    )
    assert not sans, f"tables ni sous RLS forcée ni exemptées avec leur raison : {sans}"
    exemptees_sous_rls = sorted(nom for nom in EXEMPTEES if tables.get(nom, (False, False))[0])
    assert not exemptees_sous_rls, f"exemptées mais sous RLS — retirer l'exemption : {exemptees_sous_rls}"


# ───────────────────────── les webhooks, sur un VRAI PostgreSQL ─────────────────────────


async def test_un_webhook_achemine_son_evenement_sous_rls(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """Un webhook n'a pas de principal : il est authentifié par sa signature, et agit POUR la
    plateforme. Sa session doit donc voir les projets — la RLS fail-closed les cachait tous, et
    chaque événement de CI, de CD ou de tracker était jeté comme « sans projet connu » sur
    PostgreSQL, sans que les tests (SQLite) le voient."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        # `billing-api` existe dans `a` ET `b` : on donne un slug unique pour que le routage soit
        # possible, comme le fait l'étiquette d'un vrai PipelineRun.
        (ticket,) = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == "a-1"))).scalars()
        from choregos_api.db.models import Project

        projet = await session.get(Project, ticket.project_id)
        assert projet is not None
        projet.slug = "facturation-a"
    corps = {
        "pipelineRun": {
            "metadata": {
                "name": "run-1",
                "labels": {"choregos/project": "facturation-a", "choregos/work-item": "a-1"},
            }
        }
    }
    entetes = {"ce-type": "dev.tekton.event.pipelinerun.successful.v1", "ce-id": "ev-rls-1"}
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        reponse = await client.post("/api/v1/webhooks/tekton", json=corps, headers=entetes)
    assert reponse.status_code == 202, reponse.text
    assert reponse.json()["events"] == 1, "l'événement n'a été acheminé vers aucun ticket"


async def test_un_role_de_projet_ne_devient_pas_un_role_d_organisation(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """Le principal était résolu AVANT que la portée soit posée ; la jointure sur `projects` (sous
    RLS) ne voyait alors aucun projet, et une appartenance DE PROJET arrivait sans projet — donc
    comme un rôle sur toute l'organisation. Un développeur invité sur un projet lisait les autres."""
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        a = (await session.execute(select(Organization).where(Organization.slug == "a"))).scalar_one()
        autre = Project(
            org_id=a.id, slug="secret", name="Secret", status="active", config={"slug": "secret", "org": "a"}
        )
        session.add(autre)
        invite = User(oidc_sub="dev|invite@a.test", email="invite@a.test", display_name="invité")
        session.add(invite)
        await session.flush()
        session.add(
            Membership(user_id=invite.id, org_id=a.id, project_id=deux_organisations["a"], role="developer")
        )
        secret_id = autre.id

    # Directement, comme `current_principal` : l'utilisateur lu en portée de plateforme, puis la
    # résolution — qui pose elle-même `*` avant la jointure, et borne après. Une connexion de
    # développement donnerait en plus « developer » dans l'organisation par défaut, et brouillerait
    # la preuve.
    from choregos_api.deps import _principal_from_user

    async with session_scope(orgs="*") as session:
        user = await session.get(User, invite.id)
        assert user is not None
        principal = await _principal_from_user(session, user)
    assert principal.project_roles == {"a/billing-api": "developer"}, principal.project_roles
    assert principal.org_roles == {}, (
        f"le rôle de projet est devenu un rôle d'organisation : {principal.org_roles}"
    )
    assert not principal.can(
        __import__("choregos_api.rbac", fromlist=["Permission"]).Permission.PROJECT_READ, "a", "secret"
    )
    assert secret_id


async def test_creer_une_seconde_organisation_sous_rls(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """`POST /orgs` rendait 500 sur PostgreSQL : la trace d'audit de la nouvelle organisation était
    refusée par la RLS, la session étant bornée aux organisations existantes de l'appelant."""
    from choregos_api.edition import ENTREPRISE, declarer, reinitialiser

    declarer(ENTREPRISE, fonctions=frozenset({"multi_org"}))
    try:
        async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
            await login(client, "admin@a.test")
            # l'admin de `a` n'administre pas `b` : la règle du cœur (admin de TOUTES) refuse.
            refus = await client.post("/api/v1/orgs", json={"slug": "c", "name": "C"})
            assert refus.status_code == 403
        from choregos_api.db.models import Membership, Organization, User
        from choregos_api.db.session import session_scope

        async with session_scope(orgs="*") as session:
            admin = (await session.execute(select(User).where(User.email == "admin@a.test"))).scalar_one()
            b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
            session.add(Membership(user_id=admin.id, org_id=b.id, role="org_admin"))
        async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
            await login(client, "admin@a.test")
            cree = await client.post("/api/v1/orgs", json={"slug": "c", "name": "C"})
            assert cree.status_code == 201, cree.text
    finally:
        reinitialiser()


# ───────────────────────── l'identité sous RLS (0.11) ─────────────────────────


async def test_une_organisation_ne_voit_ni_le_nom_ni_les_membres_de_l_autre(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    from choregos_api.db.models import ApiToken, Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        orgs = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            u = User(oidc_sub=f"dev|m@{slug}", email=f"membre@{slug}.test", display_name=slug)
            session.add(u)
            await session.flush()
            session.add(Membership(user_id=u.id, org_id=orgs[slug], role="developer"))
            session.add(ApiToken(user_id=u.id, name="ci", hash=f"{slug}" * 64, scopes=[]))

    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["a"]
        assert {m.org_id for m in (await session.execute(select(Membership))).scalars()} == {orgs["a"]}
        assert (await session.execute(select(User.email))).scalars().all() == ["membre@a.test"]
        assert [t.name for t in (await session.execute(select(ApiToken))).scalars()] == ["ci"]
    async with session_scope() as session:
        for modele in (Organization, Membership, User, ApiToken):
            assert (await session.execute(select(modele))).scalars().all() == [], modele.__tablename__


async def test_inviter_un_utilisateur_deja_membre_ailleurs_ne_cree_pas_de_doublon(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """L'e-mail est unique à l'échelle de l'instance ; sous la RLS de `users`, un membre de `b` est
    invisible depuis `a`, et l'invitation tentait un doublon."""
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
        ailleurs = User(oidc_sub="dev|x@b", email="partage@b.test", display_name="partagé")
        session.add(ailleurs)
        await session.flush()
        session.add(Membership(user_id=ailleurs.id, org_id=b.id, role="developer"))
        ailleurs_id = ailleurs.id
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        reponse = await client.post(
            "/api/v1/orgs/a/members", json={"email": "partage@b.test", "role": "viewer"}
        )
        assert reponse.status_code in {200, 201}, reponse.text
    async with session_scope(orgs="*") as session:
        (seul,) = (await session.execute(select(User).where(User.email == "partage@b.test"))).scalars()
        assert seul.id == ailleurs_id


async def test_un_utilisateur_sans_appartenance_se_lit_lui_meme(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """Portée vide, mais `app.current_user` : il lit son compte et ses jetons, rien d'autre."""
    import time

    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.security import sign_session

    async with session_scope(orgs="*") as session:
        seul = User(oidc_sub="dev|seul", email="seul@nulle-part.test", display_name="seul")
        session.add(seul)
        await session.flush()
        seul_id = seul.id
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        maintenant = int(time.time())
        client.cookies.set(
            "choregos_session", sign_session({"sub": seul_id, "iat": maintenant, "exp": maintenant + 600})
        )
        moi = await client.get("/api/v1/me")
        assert moi.status_code == 200 and moi.json()["email"] == "seul@nulle-part.test", moi.text
        cree = await client.post("/api/v1/me/tokens", json={"name": "cli"})
        assert cree.status_code == 201, cree.text
        assert [t["name"] for t in (await client.get("/api/v1/me/tokens")).json()] == ["cli"]


async def test_un_agent_et_ses_versions_ne_sortent_pas_de_leur_organisation(
    deux_organisations: dict[str, str],
) -> None:
    """Le registre d'agents (ADR 0033) : un agent de `b` n'existe pas pour une session bornée à `a`,
    ni ses versions, ni son épingle sur un projet."""
    from choregos_api.db.models import Agent, AgentVersion, Organization, ProjectAgent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
            agent = Agent(
                org_id=org.id, slug=f"agent-{slug}", kind="internal", display_name=slug, status="active"
            )
            session.add(agent)
            await session.flush()
            session.add(
                AgentVersion(agent_id=agent.id, org_id=org.id, version=1, spec={}, checksum="sha256:x")
            )
            session.add(
                ProjectAgent(project_id=deux_organisations[slug], org_id=org.id, agent_id=agent.id, version=1)
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(Agent.slug))).scalars().all() == ["agent-a"]
        assert len((await session.execute(select(AgentVersion))).scalars().all()) == 1
        assert len((await session.execute(select(ProjectAgent))).scalars().all()) == 1
    async with session_scope() as session:
        assert (await session.execute(select(Agent))).scalars().all() == [], "sans portée, rien"
