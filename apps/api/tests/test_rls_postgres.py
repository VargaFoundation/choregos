"""Does PostgreSQL RLS REALLY isolate one organisation from another?

Never exercised before 2026-09-24: the whole suite ran on SQLite, where the policy does not
exist, and the policy itself was fail-open. This test runs only against a real PostgreSQL
(`CHOREGOS_TEST_DATABASE_URL`): the CI starts one, locally `docs/development.md` says how.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, without_postgres

pytestmark = without_postgres


@pytest.fixture
async def two_organisations(pg_app: Any) -> dict[str, str]:
    """`a` and `b`, each with a `billing-api` project and a work item."""
    from choregos_api.db.models import Organization, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            project_row = Project(
                org_id=org.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(project_row)
            await session.flush()
            await ensure_defaults(session, project_row)
            session.add(
                WorkItem(project_id=project_row.id, tracker_key=f"{slug}-1", title="T", state="ready")
            )
            ids[slug] = project_row.id
    return ids


async def test_a_session_without_an_organisation_sees_nothing(two_organisations: dict[str, str]) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:  # no scope: it was the omission that opened everything
        assert (await session.execute(select(Project))).scalars().all() == []
        assert (await session.execute(select(WorkItem))).scalars().all() == []
    async with session_scope(orgs=["a"]) as session:
        projects = (await session.execute(select(Project))).scalars().all()
        assert [p.id for p in projects] == [two_organisations["a"]]
        work_items = (await session.execute(select(WorkItem.tracker_key))).scalars().all()
        assert work_items == ["a-1"]
        # even by identifier: the row of the other organisation does not exist for this session
        assert await session.get(Project, two_organisations["b"]) is None
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(Project))).scalars().all()) == 2


async def test_through_the_api_a_member_of_a_does_not_see_b(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """The principal bounds the session: the project of `b` does not exist for a member of `a` (404)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")  # developer of `a` only (OIDC_DEFAULT_ORG=a)
        me = (await client.get("/api/v1/me")).json()
        assert {m["org"] for m in me["memberships"]} == {"a"}

        assert (await client.get(f"/api/v1/projects/{two_organisations['a']}")).status_code == 200
        assert (await client.get(f"/api/v1/projects/{two_organisations['b']}")).status_code == 404
        assert (await client.get("/api/v1/projects/b:billing-api")).status_code == 404
        listing = (await client.get("/api/v1/orgs/a/projects")).json()
        assert [p["slug"] for p in listing["items"]] == ["billing-api"]


async def test_an_administrator_creates_a_project_in_their_organisation(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """The INSERT passes the policy (judged on `org_id`, not on a sub-select that does not see the row)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        created = await client.post(
            "/api/v1/orgs/a/projects",
            json={"slug": "fresh", "name": "Fresh", "config": {"slug": "fresh", "org": "a"}},
        )
        assert created.status_code == 201, created.text
        assert (
            await client.post(
                "/api/v1/orgs/b/projects",
                json={"slug": "intruder", "name": "Intruder", "config": {"slug": "intruder", "org": "b"}},
            )
        ).status_code in {403, 404}  # 404 under the identity RLS: `b` is invisible


async def test_a_run_token_sees_its_run_whatever_the_organisation(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope(orgs="*") as session:
        item = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == "b-1"))).scalar_one()
        run = Run(
            id="b-1-t-implement-1",
            work_item_id=item.id,
            project_id=two_organisations["b"],
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
    run_token = mint_run_token(
        "b-1-t-implement-1", project_slug="billing-api", work_item_key="b-1", ttl_minutes=5
    )
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/internal/runs/b-1-t-implement-1/ticket", headers={"Authorization": f"Bearer {run_token}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["key"] == "b-1"


@pytest.fixture
async def three_traces(two_organisations: dict[str, str]) -> dict[str, str]:
    """A trace for `a`, one for `b`, one for the platform (no organisation)."""
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
                target_id=two_organisations[slug],
                payload={},
                ts=utcnow(),
            )
            session.add(trace)
            await session.flush()
            ids[slug] = trace.id
        platform = AuditLog(
            actor_id="u-a",
            actor_kind="user",
            org_id=None,
            action="auth.login",
            target_type="user",
            target_id="u-a",
            payload={},
            ts=utcnow(),
        )
        session.add(platform)
        await session.flush()
        ids["platform"] = platform.id
    return ids


async def test_the_audit_trace_of_an_organisation_is_invisible_to_the_other(
    pg_app: Any, three_traces: dict[str, str]
) -> None:
    """`audit_log` was the last mutation table outside RLS, and without an organisation column."""
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope(orgs=["a"]) as session:
        seen = (await session.execute(select(AuditLog.id))).scalars().all()
        assert seen == [three_traces["a"]], "a session bounded to `a` sees something other than `a`"
    async with session_scope() as session:  # no scope: the omission must open nothing
        assert (await session.execute(select(AuditLog))).scalars().all() == []
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(AuditLog))).scalars().all()) == 3


async def test_through_the_api_member_of_both_but_audit_read_in_only_one(
    pg_app: Any, three_traces: dict[str, str]
) -> None:
    """The case RLS alone does NOT cover, and the one that leaked.

    A principal who is a member of `a` **and** of `b` has a session bounded to both: the policy
    therefore lets the traces of both through. Only the route can tell "member of `b`" from
    "allowed to read the audit of `b`". Before 2026-09-26 it told nothing apart: `GET /audit`
    returned `select(AuditLog)` without any `where`, and `audit:read` in one organisation gave the
    audit of all the others.

    Removing the filter of `routers/admin.py` turns this test red, and this test alone.
    """
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")  # ORG_ADMIN of `a`, hence `audit:read` on `a`
        async with session_scope(orgs="*") as session:
            user = (await session.execute(select(User).where(User.email == "admin@a.test"))).scalar_one()
            org_b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
            # a mere reader at `b`: the session will see it, the permission will not
            session.add(Membership(user_id=user.id, org_id=org_b.id, project_id=None, role="viewer"))

        me = (await client.get("/api/v1/me")).json()
        assert {m["org"] for m in me["memberships"]} == {"a", "b"}, "the principal must carry both"

        page = await client.get("/api/v1/audit")
        assert page.status_code == 200, page.text
        ids = {line["id"] for line in page.json()["items"]}
        assert three_traces["b"] not in ids, "the audit of `b` leaks to a mere reader of `b`"
        assert three_traces["platform"] not in ids, "a platform event leaks"
        assert three_traces["a"] in ids, "the audit of `a` must stay readable by its administrator"


# ─────────── what attaches to a project: run events, deployments, keys ───────────


@pytest.fixture
async def attached(two_organisations: dict[str, str]) -> None:
    """For each organisation: a run and its event, a release and its deployment, a key."""
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import Deployment, GatewayKeyRow, Release, Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug, project_row in two_organisations.items():
            (work_item,) = (
                await session.execute(select(WorkItem).where(WorkItem.project_id == project_row))
            ).scalars()
            session.add(
                Run(
                    id=f"run-{slug}",
                    work_item_id=work_item.id,
                    project_id=project_row,
                    stage_role="implement",
                )
            )
            release = Release(project_id=project_row, env="prod")
            session.add(release)
            await session.flush()
            session.add(RunEvent(run_id=f"run-{slug}", seq=1, type="agent/message", payload={}, ts=utcnow()))
            session.add(Deployment(release_id=release.id, env="prod", status="succeeded"))
            session.add(GatewayKeyRow(key_id=f"key-{slug}", run_id=f"run-{slug}", project_id=project_row))


async def test_the_attached_tables_show_only_the_organisation_of_the_session(attached: None) -> None:
    from choregos_api.db.models import Deployment, GatewayKeyRow, Release, RunEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(RunEvent.run_id))).scalars().all() == ["run-a"]
        assert (await session.execute(select(GatewayKeyRow.key_id))).scalars().all() == ["key-a"]
        deployments = (
            (
                await session.execute(
                    select(Release.project_id).join(Deployment, Deployment.release_id == Release.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(deployments) == 1
        assert len((await session.execute(select(Deployment))).scalars().all()) == 1
    async with session_scope() as session:
        for model in (RunEvent, Deployment, GatewayKeyRow):
            assert (await session.execute(select(model))).scalars().all() == [], model.__tablename__
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(RunEvent))).scalars().all()) == 2


async def test_a_session_of_a_cannot_write_an_event_on_the_run_of_b(attached: None) -> None:
    """`USING` serves as `WITH CHECK` for the INSERT: a row is not attributed to the other organisation."""
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="row-level security"):
        async with session_scope(orgs=["a"]) as session:
            session.add(RunEvent(run_id="run-b", seq=2, type="agent/message", payload={}, ts=utcnow()))


#: The tables OUTSIDE RLS, and why. Every other table must be under forced RLS: a table
#: added tomorrow without a policy turns `test_every_table_is_under_rls_or_exempted` red.
EXEMPTED = {
    # Instance catalogues, openly readable by decision (`test_bare_routers.py`):
    # no organisation data.
    "agent_backends": "instance catalogue",
    "templates": "instance catalogue",
    "model_profiles": "instance catalogue (only `scope=platform` rows are written)",
    "webhook_deliveries": "deduplication digests, no content or organisation",
    "alembic_version": "Alembic table",
    "org_slug_aliases": "address -> organisation id, no content: resolved BEFORE any scope exists (ADR 0090)",
}


async def test_named_memories_and_their_facts_are_invisible_across_organisations(
    two_organisations: dict[str, str],
) -> None:
    """S40-11: `memory_stores` and the facts of a global memory (no project) are under forced RLS, keyed on
    the organisation; a project fact is keyed the same way since `memory_facts.org_id`."""
    from choregos_api.db.models import MemoryFact, MemoryStore, Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        orgs = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug, org_id in orgs.items():
            store = MemoryStore(
                org_id=org_id,
                slug="lessons",
                display_name=f"{slug} lessons",
                scope="global",
                owner_key="org",
                reach="org",
                projects=[],
                status="active",
            )
            session.add(store)
            await session.flush()
            for project_id, memory_store_id, subject in (
                (None, store.id, f"{slug}-global"),
                (two_organisations[slug], None, f"{slug}-project"),
            ):
                session.add(
                    MemoryFact(
                        org_id=org_id,
                        project_id=project_id,
                        memory_store_id=memory_store_id,
                        kind="decision",
                        subject=subject,
                        content="c",
                    )
                )
    async with session_scope() as session:  # no scope: nothing
        assert (await session.execute(select(MemoryStore))).scalars().all() == []
        assert (await session.execute(select(MemoryFact))).scalars().all() == []
    async with session_scope(orgs=["a"]) as session:
        assert [s.display_name for s in (await session.execute(select(MemoryStore))).scalars()] == [
            "a lessons"
        ]
        subjects = sorted((await session.execute(select(MemoryFact.subject))).scalars())
        assert subjects == ["a-global", "a-project"]
    async with session_scope(orgs=["b"]) as session:
        assert sorted((await session.execute(select(MemoryFact.subject))).scalars()) == [
            "b-global",
            "b-project",
        ]
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(MemoryStore))).scalars().all()) == 2


async def test_a_renamed_organisation_is_still_seen_and_its_old_slug_still_scopes(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """RLS compares CURRENT slugs. After `a` becomes `z`: the new slug sees the rows, and a scope built
    from the OLD slug (a workflow payload written before the rename) is resolved to the new one instead
    of seeing nothing (ADR 0090). An alias never leaks another organisation's rows."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.org_aliases import current_slug, keep_old_slug
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "a"))).scalar_one()
        org.slug = "z"
        await keep_old_slug(session, org, "a", "test")
    async with session_scope(orgs=["z"]) as session:
        assert [p.slug for p in (await session.execute(select(Project))).scalars()] == ["billing-api"]
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["z"]
    async with session_scope(orgs=["a"]) as session:  # the old slug, written down earlier
        assert [p.id for p in (await session.execute(select(Project))).scalars()] == [two_organisations["a"]]
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["z"]
    async with session_scope(orgs=["b"]) as session:
        assert [p.id for p in (await session.execute(select(Project))).scalars()] == [two_organisations["b"]]
    async with session_scope(orgs=["nobody"]) as session:
        assert (await session.execute(select(Project))).scalars().all() == []
    async with session_scope(orgs=["b"]) as session:
        # resolving an alias works under a narrow scope, and leaves that scope as it was
        assert await current_slug(session, "a") == "z"
        assert [p.id for p in (await session.execute(select(Project))).scalars()] == [two_organisations["b"]]


async def test_every_table_is_under_rls_or_exempted(pg_app: Any) -> None:
    from choregos_api.db.session import session_scope
    from sqlalchemy import text

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace"
                )
            )
        ).all()
    tables = {name: (rls, forced) for name, rls, forced in rows}
    assert len(tables) >= 25, f"only {len(tables)} tables: did the migrations run?"
    unprotected = sorted(
        name for name, (rls, forced) in tables.items() if not (rls and forced) and name not in EXEMPTED
    )
    assert not unprotected, f"tables neither under forced RLS nor exempted with their reason: {unprotected}"
    exempted_under_rls = sorted(name for name in EXEMPTED if tables.get(name, (False, False))[0])
    assert not exempted_under_rls, f"exempted but under RLS: remove the exemption: {exempted_under_rls}"


# ───────────────────────── the webhooks, on a REAL PostgreSQL ─────────────────────────


async def test_a_webhook_routes_its_event_under_rls(pg_app: Any, two_organisations: dict[str, str]) -> None:
    """A webhook has no principal: it is authenticated by its signature, and acts FOR the platform.
    Its session must therefore see the projects: the fail-closed RLS hid them all, and every CI, CD
    or tracker event was dropped as "no known project" on PostgreSQL, without the tests (SQLite)
    seeing it."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        # `billing-api` exists in `a` AND `b`: we give a unique slug so that routing is
        # possible, as the label of a real PipelineRun does.
        (work_item,) = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == "a-1"))
        ).scalars()
        from choregos_api.db.models import Project

        project_row = await session.get(Project, work_item.project_id)
        assert project_row is not None
        project_row.slug = "invoicing-a"
    body = {
        "pipelineRun": {
            "metadata": {
                "name": "run-1",
                "labels": {"choregos/project": "invoicing-a", "choregos/work-item": "a-1"},
            }
        }
    }
    headers = {"ce-type": "dev.tekton.event.pipelinerun.successful.v1", "ce-id": "ev-rls-1"}
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        response = await client.post("/api/v1/webhooks/tekton", json=body, headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["events"] == 1, "the event was routed to no work item"


async def test_a_project_role_does_not_become_an_organisation_role(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """The principal was resolved BEFORE the scope was set; the join on `projects` (under RLS) then
    saw no project, and a PROJECT membership arrived without a project, hence as a role on the whole
    organisation. A developer invited on one project read the others."""
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        a = (await session.execute(select(Organization).where(Organization.slug == "a"))).scalar_one()
        other = Project(
            org_id=a.id, slug="secret", name="Secret", status="active", config={"slug": "secret", "org": "a"}
        )
        session.add(other)
        invited = User(oidc_sub="dev|invited@a.test", email="invited@a.test", display_name="invited")
        session.add(invited)
        await session.flush()
        session.add(
            Membership(user_id=invited.id, org_id=a.id, project_id=two_organisations["a"], role="developer")
        )
        secret_id = other.id

    # Directly, like `current_principal`: the user read in platform scope, then the
    # resolution, which itself sets `*` before the join, and bounds afterwards. A development
    # login would additionally give "developer" in the default organisation, and blur the
    # proof.
    from choregos_api.deps import _principal_from_user

    async with session_scope(orgs="*") as session:
        user = await session.get(User, invited.id)
        assert user is not None
        principal = await _principal_from_user(session, user)
    assert principal.project_roles == {"a/billing-api": "developer"}, principal.project_roles
    assert principal.org_roles == {}, f"the project role became an organisation role: {principal.org_roles}"
    assert not principal.can(
        __import__("choregos_api.rbac", fromlist=["Permission"]).Permission.PROJECT_READ, "a", "secret"
    )
    assert secret_id


async def test_creating_a_second_organisation_under_rls(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """`POST /orgs` answered 500 on PostgreSQL: the audit trace of the new organisation was refused
    by RLS, the session being bounded to the caller's existing organisations."""
    from choregos_api.edition import ENTERPRISE, declare, reset

    declare(ENTERPRISE, features=frozenset({"multi_org"}))
    try:
        async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
            await login(client, "admin@a.test")
            # the admin of `a` does not administer `b`: the core rule (admin of ALL) refuses.
            refusal = await client.post("/api/v1/orgs", json={"slug": "c", "name": "C"})
            assert refusal.status_code == 403
        from choregos_api.db.models import Membership, Organization, User
        from choregos_api.db.session import session_scope

        async with session_scope(orgs="*") as session:
            admin = (await session.execute(select(User).where(User.email == "admin@a.test"))).scalar_one()
            b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
            session.add(Membership(user_id=admin.id, org_id=b.id, role="org_admin"))
        async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
            await login(client, "admin@a.test")
            created = await client.post("/api/v1/orgs", json={"slug": "c", "name": "C"})
            assert created.status_code == 201, created.text
    finally:
        reset()


# ───────────────────────── identity under RLS (0.11) ─────────────────────────


async def test_an_organisation_sees_neither_the_name_nor_the_members_of_the_other(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    from choregos_api.db.models import ApiToken, Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        orgs = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            u = User(oidc_sub=f"dev|m@{slug}", email=f"member@{slug}.test", display_name=slug)
            session.add(u)
            await session.flush()
            session.add(Membership(user_id=u.id, org_id=orgs[slug], role="developer"))
            session.add(ApiToken(user_id=u.id, name="ci", hash=f"{slug}" * 64, scopes=[]))

    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["a"]
        assert {m.org_id for m in (await session.execute(select(Membership))).scalars()} == {orgs["a"]}
        assert (await session.execute(select(User.email))).scalars().all() == ["member@a.test"]
        assert [t.name for t in (await session.execute(select(ApiToken))).scalars()] == ["ci"]
    async with session_scope() as session:
        for model in (Organization, Membership, User, ApiToken):
            assert (await session.execute(select(model))).scalars().all() == [], model.__tablename__


async def test_inviting_a_user_already_a_member_elsewhere_creates_no_duplicate(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """The e-mail is unique across the instance; under the RLS of `users`, a member of `b` is
    invisible from `a`, and the invitation tried a duplicate."""
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        b = (await session.execute(select(Organization).where(Organization.slug == "b"))).scalar_one()
        elsewhere = User(oidc_sub="dev|x@b", email="shared@b.test", display_name="shared")
        session.add(elsewhere)
        await session.flush()
        session.add(Membership(user_id=elsewhere.id, org_id=b.id, role="developer"))
        elsewhere_id = elsewhere.id
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        response = await client.post(
            "/api/v1/orgs/a/members", json={"email": "shared@b.test", "role": "viewer"}
        )
        assert response.status_code in {200, 201}, response.text
    async with session_scope(orgs="*") as session:
        (alone,) = (await session.execute(select(User).where(User.email == "shared@b.test"))).scalars()
        assert alone.id == elsewhere_id


async def test_a_user_without_a_membership_reads_themselves(
    pg_app: Any, two_organisations: dict[str, str]
) -> None:
    """Empty scope, but `app.current_user`: they read their account and their tokens, nothing else."""
    import time

    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.security import sign_session

    async with session_scope(orgs="*") as session:
        alone = User(oidc_sub="dev|alone", email="alone@nowhere.test", display_name="alone")
        session.add(alone)
        await session.flush()
        alone_id = alone.id
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        now = int(time.time())
        client.cookies.set("choregos_session", sign_session({"sub": alone_id, "iat": now, "exp": now + 600}))
        me = await client.get("/api/v1/me")
        assert me.status_code == 200 and me.json()["email"] == "alone@nowhere.test", me.text
        created = await client.post("/api/v1/me/tokens", json={"name": "cli"})
        assert created.status_code == 201, created.text
        assert [t["name"] for t in (await client.get("/api/v1/me/tokens")).json()] == ["cli"]


async def test_an_agent_and_its_versions_do_not_leave_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """The agent registry (ADR 0033): an agent of `b` does not exist for a session bounded to `a`,
    nor its versions, nor its pin on a project."""
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
                ProjectAgent(project_id=two_organisations[slug], org_id=org.id, agent_id=agent.id, version=1)
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(Agent.slug))).scalars().all() == ["agent-a"]
        assert len((await session.execute(select(AgentVersion))).scalars().all()) == 1
        assert len((await session.execute(select(ProjectAgent))).scalars().all()) == 1
    async with session_scope() as session:
        assert (await session.execute(select(Agent))).scalars().all() == [], "without a scope, nothing"


async def test_a_skill_and_its_versions_do_not_leave_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """The skill library (ADR 0033): a skill of `b` does not exist for `a`."""
    from choregos_api.db.models import Organization, Skill, SkillVersion
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
            skill = Skill(org_id=org.id, slug=f"skill-{slug}", status="active")
            session.add(skill)
            await session.flush()
            session.add(
                SkillVersion(skill_id=skill.id, org_id=org.id, version=1, files={}, digest="sha256:x")
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(Skill.slug))).scalars().all() == ["skill-a"]
        assert len((await session.execute(select(SkillVersion))).scalars().all()) == 1


async def test_a_knowledge_base_and_its_documents_stay_in_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """Knowledge bases (ADR 0051): a base of `b`, and its documents, do not exist for `a`."""
    from choregos_api.db.models import KnowledgeBase, KnowledgeDocument, Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
            base = KnowledgeBase(
                org_id=org.id, slug=f"kb-{slug}", name=slug, scope="org", projects=[], groups=[]
            )
            session.add(base)
            await session.flush()
            session.add(
                KnowledgeDocument(
                    org_id=org.id,
                    knowledge_base_id=base.id,
                    filename="n.md",
                    title="n",
                    mime="text/markdown",
                    size_bytes=1,
                    digest=f"sha256:{slug}",
                    markdown=f"secret of {slug}",
                )
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(KnowledgeBase.slug))).scalars().all() == ["kb-a"]
        assert (await session.execute(select(KnowledgeDocument.markdown))).scalars().all() == ["secret of a"]
    async with session_scope() as session:
        assert (await session.execute(select(KnowledgeDocument))).scalars().all() == [], (
            "without a scope, nothing"
        )


async def test_org_secrets_are_isolated(two_organisations: dict[str, str]) -> None:
    """Sealed connector secrets (ADR 0058): org `a` reads none of `b`'s, writes none into it, and
    a session without a scope sees nothing — the AAD is a second lock, not the first."""
    import base64
    import os

    from choregos_api.db.models import Organization, OrgSecret
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import SealedDbSecretStore, parse_keyring
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    store = SealedDbSecretStore(parse_keyring("k1:" + base64.b64encode(os.urandom(32)).decode()))
    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            await store.put(session, ids[slug], "connector/vendor/token", f"value-of-{slug}", "x@y.z")
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(OrgSecret.org_id))).scalars().all() == [ids["a"]]
        assert await store.get_many(session, ids["a"], ["connector/vendor/token"]) == {
            "connector/vendor/token": "value-of-a"
        }
        assert await store.meta(session, ids["b"], ["connector/vendor/token"]) == {}, (
            "b's row does not exist for a"
        )
        touched = await session.execute(
            update(OrgSecret).where(OrgSecret.org_id == ids["b"]).values(kek_id="zz")
        )
        assert touched.rowcount == 0, "an UPDATE across organisations touches nothing"
    with pytest.raises(DBAPIError):  # a write INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            await store.put(session, ids["b"], "connector/vendor/other", "stolen", "x@y.z")
    async with session_scope() as session:
        assert (await session.execute(select(OrgSecret))).scalars().all() == [], "no scope, nothing"
    async with session_scope(orgs="*") as session:
        rows = (await session.execute(select(OrgSecret))).scalars().all()
        assert sorted(r.kek_id for r in rows) == ["k1", "k1"]


async def test_github_installations_are_isolated(two_organisations: dict[str, str]) -> None:
    """GitHub App installations (ADR 0073): a claimed row is its organisation's, an unclaimed one is
    the platform's, and an organisation cannot claim into another by writing the row."""
    from choregos_api.db.models import GitHubInstallation, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for number, (login, org) in enumerate([("a-inc", "a"), ("b-inc", "b"), ("free-co", None)], start=1):
            session.add(
                GitHubInstallation(
                    id=number,
                    account_login=login,
                    account_key=login,
                    account_id=number,
                    account_type="Organization",
                    org_id=ids[org] if org else None,
                )
            )
    async with session_scope(orgs=["a"]) as session:
        seen = (await session.execute(select(GitHubInstallation.account_login))).scalars().all()
        assert seen == ["a-inc"], "a sees its own claim, neither b's nor the unclaimed one"
    with pytest.raises(DBAPIError):  # a claim INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            session.add(
                GitHubInstallation(
                    id=9,
                    account_login="x",
                    account_key="x",
                    account_id=9,
                    account_type="User",
                    org_id=ids["b"],
                )
            )
    async with session_scope() as session:
        assert (await session.execute(select(GitHubInstallation))).scalars().all() == [], "no scope, nothing"
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(GitHubInstallation))).scalars().all()) == 3


async def test_network_decisions_stay_in_their_organisation(two_organisations: dict[str, str]) -> None:
    """Network decisions (ADR 0070): what org `b`'s runs reached is invisible to `a`, and a session
    scoped to `a` cannot write a decision into `b`."""
    from choregos_api.db.models import NetworkDecision, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    def row(org_id: str, project_id: str, host: str) -> NetworkDecision:
        now = datetime.now(UTC)
        return NetworkDecision(
            org_id=org_id,
            project_id=project_id,
            layer="proxy",
            host=host,
            port=443,
            decision="denied",
            reason="not_allowed",
            first_at=now,
            last_at=now,
        )

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            session.add(row(ids[slug], two_organisations[slug], f"host-of-{slug}.example"))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(NetworkDecision.host))).scalars().all() == ["host-of-a.example"]
    with pytest.raises(DBAPIError):  # a write INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            session.add(row(ids["b"], two_organisations["b"], "stolen.example"))
    async with session_scope() as session:
        assert (await session.execute(select(NetworkDecision))).scalars().all() == [], "no scope, nothing"


async def test_received_events_stay_in_their_organisation(two_organisations: dict[str, str]) -> None:
    """Received webhook events (S42-06f): org `a` reads none of `b`'s, writes none into it, and a
    session with no scope reads nothing."""
    from choregos_api.db.models import Organization, ReceivedEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    def row(org_id: str, project_id: str, key: str) -> ReceivedEvent:
        return ReceivedEvent(
            org_id=org_id,
            project_id=project_id,
            work_item_key=key,
            source="jira",
            event_type="tracker.item.labeled",
            outcome="signalled",
            summary=key,
            received_at=datetime.now(UTC),
        )

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            session.add(row(ids[slug], two_organisations[slug], f"{slug.upper()}-1"))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(ReceivedEvent.work_item_key))).scalars().all() == ["A-1"]
    with pytest.raises(DBAPIError):  # a write INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            session.add(row(ids["b"], two_organisations["b"], "STOLEN-1"))
    async with session_scope() as session:
        assert (await session.execute(select(ReceivedEvent))).scalars().all() == [], "no scope, nothing"


async def test_trigger_tokens_stay_in_their_organisation(two_organisations: dict[str, str]) -> None:
    """HTTP trigger tokens (S56-01): org `a` reads none of `b`'s digests, writes none into it, and a
    session with no scope reads nothing."""
    from choregos_api.db.models import Organization, TriggerToken
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    def row(org_id: str, project_id: str, name: str) -> TriggerToken:
        return TriggerToken(org_id=org_id, project_id=project_id, name=name, token_sha256="0" * 64)

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            session.add(row(ids[slug], two_organisations[slug], f"incident-{slug}"))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(TriggerToken.name))).scalars().all() == ["incident-a"]
    with pytest.raises(DBAPIError):  # a write INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            session.add(row(ids["b"], two_organisations["b"], "stolen"))
    async with session_scope() as session:
        assert (await session.execute(select(TriggerToken))).scalars().all() == [], "no scope, nothing"


async def test_machine_accounts_stay_in_their_organisation(two_organisations: dict[str, str]) -> None:
    """Machine accounts (ADR 0131): org `a` reads none of `b`'s, writes none into it, and a session
    with no scope reads nothing."""
    from choregos_api.db.models import MachineAccount, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        owners = {}
        for slug in ("a", "b"):
            user = User(email=f"m.{slug}@machines.choregos.invalid", display_name=slug, is_service=True)
            session.add(user)
            await session.flush()
            owners[slug] = user.id
            session.add(
                MachineAccount(
                    org_id=ids[slug],
                    project_id=two_organisations[slug],
                    user_id=user.id,
                    slug=f"app-{slug}",
                    display_name=slug,
                )
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(MachineAccount.slug))).scalars().all() == ["app-a"]
    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["a"]) as session:
            session.add(
                MachineAccount(
                    org_id=ids["b"],
                    project_id=two_organisations["b"],
                    user_id=owners["b"],
                    slug="stolen",
                    display_name="stolen",
                )
            )
    async with session_scope() as session:
        assert (await session.execute(select(MachineAccount))).scalars().all() == [], "no scope, nothing"


async def test_org_groups_and_their_members_stay_in_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """An organisation's groups and who is in them (ADR 0160): org `a` reads none of `b`'s, writes none
    into it, and a session with no scope reads nothing."""
    from choregos_api.db.models import Organization, OrgGroup, OrgGroupMember, User
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        groups = {}
        for slug in ("a", "b"):
            user = User(email=f"captain@{slug}.test", display_name=slug)
            group = OrgGroup(org_id=ids[slug], slug=f"captains-{slug}", name=slug, source="internal")
            session.add_all([user, group])
            await session.flush()
            groups[slug] = group.id
            session.add(OrgGroupMember(org_id=ids[slug], group_id=group.id, user_id=user.id))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(OrgGroup.slug))).scalars().all() == ["captains-a"]
        assert (await session.execute(select(OrgGroupMember.group_id))).scalars().all() == [groups["a"]]
    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["a"]) as session:
            session.add(OrgGroup(org_id=ids["b"], slug="stolen", name="stolen", source="internal"))
    async with session_scope() as session:
        assert (await session.execute(select(OrgGroup))).scalars().all() == [], "no scope, nothing"
        assert (await session.execute(select(OrgGroupMember))).scalars().all() == [], "no scope, nothing"


async def test_assistant_installations_stay_in_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """Assistant installations and their bindings (ADR 0133): org `a` reads none of `b`'s, writes none into
    it, and a session with no scope reads nothing."""
    from choregos_api.db.models import (
        AssistantInstallation,
        AssistantToolBinding,
        Organization,
        OrgConnector,
    )
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import DBAPIError

    def installation(org_id: str, slug: str) -> AssistantInstallation:
        return AssistantInstallation(
            org_id=org_id,
            slug=slug,
            plugin="p",
            plugin_version="1",
            manifest_digest="sha256:0",
            assistant_name="a",
        )

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            row = installation(ids[slug], f"assistant-{slug}")
            connector = OrgConnector(org_id=ids[slug], name=f"c-{slug}", kind="mcp", type="mcp")
            session.add_all([row, connector])
            await session.flush()
            session.add(
                AssistantToolBinding(
                    org_id=ids[slug],
                    installation_id=row.id,
                    need="n",
                    connector_id=connector.id,
                    operations=["o"],
                    identity="service",
                )
            )
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(AssistantInstallation.slug))).scalars().all() == ["assistant-a"]
        assert len((await session.execute(select(AssistantToolBinding))).scalars().all()) == 1
    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["a"]) as session:
            session.add(installation(ids["b"], "stolen"))
    async with session_scope() as session:
        assert (await session.execute(select(AssistantInstallation))).scalars().all() == []
        assert (await session.execute(select(AssistantToolBinding))).scalars().all() == []


async def test_task_subscriptions_are_the_subscribers_own_in_their_organisation(
    two_organisations: dict[str, str],
) -> None:
    """Task subscriptions (ADR 0137): the organisation's scope is not enough, only the subscriber (or the
    platform scope, the scheduler) reads a row; org `a` reads none of `b`'s."""
    from choregos_api.db.models import AssistantInstallation, Organization, TaskSubscription, User
    from choregos_api.db.session import name_the_user, session_scope

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        users = {n: User(oidc_sub=f"dev|{n}", email=f"{n}@t.test", display_name=n) for n in ("ann", "bob")}
        session.add_all(users.values())
        await session.flush()
        for slug in ("a", "b"):
            row = AssistantInstallation(
                org_id=ids[slug],
                slug=f"assistant-{slug}",
                plugin="p",
                plugin_version="1",
                manifest_digest="sha256:0",
                assistant_name="a",
            )
            session.add(row)
            await session.flush()
            for name, user in users.items():
                session.add(
                    TaskSubscription(
                        org_id=ids[slug],
                        installation_id=row.id,
                        user_id=user.id,
                        task=f"task_{name}",
                        trigger={"kind": "on_demand"},
                        seen_keys=[],
                    )
                )
        ann = users["ann"].id
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, ann)
        assert (await session.execute(select(TaskSubscription.task))).scalars().all() == ["task_ann"]
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(TaskSubscription))).scalars().all() == [], "no person, no row"
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(TaskSubscription))).scalars().all()) == 4
    async with session_scope() as session:
        assert (await session.execute(select(TaskSubscription))).scalars().all() == []


async def test_org_egress_rules_are_isolated(two_organisations: dict[str, str]) -> None:
    """The egress allowlist (S39-22): org `a` reads none of `b`'s rules, writes none into it, and a
    session without a scope sees nothing."""
    from choregos_api.db.models import Organization, OrgEgressRule
    from choregos_api.db.session import session_scope
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    async with session_scope(orgs="*") as session:
        ids = {o.slug: o.id for o in (await session.execute(select(Organization))).scalars()}
        for slug in ("a", "b"):
            session.add(OrgEgressRule(org_id=ids[slug], host=f"{slug}.example.test", status="allowed"))
    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(OrgEgressRule.host))).scalars().all() == ["a.example.test"]
        touched = await session.execute(
            update(OrgEgressRule).where(OrgEgressRule.org_id == ids["b"]).values(status="revoked")
        )
        assert touched.rowcount == 0, "an UPDATE across organisations touches nothing"
    with pytest.raises(DBAPIError):  # a write INTO b from a's session is refused by the policy
        async with session_scope(orgs=["a"]) as session:
            session.add(OrgEgressRule(org_id=ids["b"], host="stolen.example.test", status="allowed"))
    async with session_scope() as session:
        assert (await session.execute(select(OrgEgressRule))).scalars().all() == [], "no scope, nothing"
