"""Rights on knowledge bases (S42-11, ADR 0081).

Reach (scope, `restricted`) decides who reads a base and which runs are served it; roles (viewer,
editor, owner, from grants or ownership) decide who changes it. One server function, `decide`, answers
both, and the DTO's `can` says what the caller may do: the tests check the two agree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga/knowledge"
SOURCE = {"kind": "web", "name": "docs", "config": {"url": "https://example.org/docs"}}


async def _checkout(client: AsyncClient) -> None:
    payload = {
        "slug": "checkout-web",
        "name": "Checkout web",
        "config": {
            "slug": "checkout-web",
            "org": "varga",
            "repo": {
                "url": "https://github.com/varga/checkout-web.git",
                "default_branch": "main",
                "language": "python",
            },
        },
    }
    created = await client.post("/api/v1/orgs/varga/projects", json=payload)
    assert created.status_code == 201, created.text


async def _shape(
    email: str,
    org_role: str | None,
    projects: dict[str, str],
    groups: list[str],
    org_slug: str = "varga",
) -> str:
    """Gives the user exactly these memberships (the development sign-in adds a developer role)."""
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups
        await session.execute(delete(Membership).where(Membership.user_id == user.id))
        org = (await session.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one()
        if org_role:
            session.add(Membership(user_id=user.id, org_id=org.id, role=org_role))
        for slug, role in projects.items():
            project_id = (
                await session.execute(
                    select(Project.id).where(Project.org_id == org.id, Project.slug == slug)
                )
            ).scalar_one()
            session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))
        return str(user.id)


@asynccontextmanager
async def _as(
    client: AsyncClient,
    email: str,
    *,
    org_role: str | None = None,
    projects: dict[str, str] | None = None,
    groups: list[str] | None = None,
) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        await _shape(email, org_role, projects or {}, groups or [])
        yield c


async def _uid(client: AsyncClient, email: str, **shape: Any) -> str:
    """Creates the person (a first sign-in), shapes their memberships, and gives their id."""
    async with _as(client, email, **shape):
        pass
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str((await session.execute(select(User.id).where(User.email == email))).scalar_one())


async def _admin_base(client: AsyncClient, project: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """`fiches`: owned by billing-api, so scope `projects` [billing-api] and `owner_project` set."""
    created = await client.post(
        f"/api/v1/projects/{project['id']}/knowledge", json={"slug": "fiches", "name": "Fiches", **extra}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _grant(client: AsyncClient, *grants: dict[str, str], slug: str = "fiches") -> Any:
    return await client.put(f"{ORG}/{slug}/grants", json={"grants": list(grants)})


def _ok(response: Any) -> bool:
    return bool(response.status_code not in {403, 404})


async def _outcomes(c: AsyncClient) -> dict[str, bool]:
    """What this caller can actually do, by trying (the destructive gestures last)."""
    done: dict[str, bool] = {}
    done["read"] = (await c.get(f"{ORG}/fiches")).status_code == 200
    done["upload"] = _ok(await c.post(f"{ORG}/fiches/documents", files={"file": ("n.md", b"# N\n")}))
    done["source"] = _ok(await c.post(f"{ORG}/fiches/sources", json=SOURCE))
    done["sync"] = _ok(await c.post(f"{ORG}/fiches/sync"))
    done["distill"] = _ok(await c.post(f"{ORG}/fiches/distill"))
    listed = await c.get(f"{ORG}/fiches/grants")
    current = listed.json()["grants"] if listed.status_code == 200 else []
    done["share"] = _ok(await c.put(f"{ORG}/fiches/grants", json={"grants": current}))
    done["widen"] = _ok(await c.patch(f"{ORG}/fiches", json={"scope": "org"}))
    done["archive"] = _ok(await c.patch(f"{ORG}/fiches", json={"status": "archived"}))
    return done


ALL = {"read": True, "upload": True, "source": True, "sync": True, "distill": True, "share": True}
EDITS = {"read": True, "upload": True, "source": True, "sync": True, "distill": True, "share": False}
READS = {"read": True, "upload": False, "source": False, "sync": False, "distill": False, "share": False}
NONE = dict.fromkeys(ALL, False)
#: caller -> (what they may do). `widen` is the administrator's alone; `archive` needs the owner role.
EXPECTED: dict[str, dict[str, bool]] = {
    "admin": {**ALL, "widen": True, "archive": True},
    "project owner": {**ALL, "widen": False, "archive": True},
    "project developer": {**READS, "widen": False, "archive": False},
    "project viewer": {**READS, "widen": False, "archive": False},
    "user editor grant": {**EDITS, "widen": False, "archive": False},
    "user viewer grant": {**READS, "widen": False, "archive": False},
    "user owner grant": {**ALL, "widen": False, "archive": True},
    "group editor grant": {**EDITS, "widen": False, "archive": False},
    "other project's member": {**NONE, "widen": False, "archive": False},
}


PEOPLE: dict[str, dict[str, Any]] = {
    "project owner": {"projects": {"billing-api": "project_owner"}},
    "project developer": {"projects": {"billing-api": "developer"}},
    "project viewer": {"projects": {"billing-api": "viewer"}},
    "user editor grant": {"projects": {"checkout-web": "viewer"}},
    "user viewer grant": {"projects": {"checkout-web": "viewer"}},
    "user owner grant": {"projects": {"checkout-web": "viewer"}},
    "group editor grant": {"projects": {"checkout-web": "viewer"}, "groups": ["writers"]},
    "other project's member": {"projects": {"checkout-web": "viewer"}},
}


def _email(who: str) -> str:
    return who.replace(" ", ".").replace("'", "") + "@varga.dev"


@pytest.fixture
async def two_projects(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    await _checkout(client)
    return project


@pytest.mark.parametrize("who", list(EXPECTED))
async def test_what_each_caller_may_do_and_the_dto_agrees(
    client: AsyncClient, two_projects: dict[str, Any], who: str
) -> None:
    """The matrix of ADR 0081, and `can` in the DTO equals what the routes enforce."""
    await _admin_base(client, two_projects)
    ids = {
        name: await _uid(client, _email(name), **PEOPLE[name])
        for name in ("user editor grant", "user viewer grant", "user owner grant")
    }
    await login(client, "admin@varga.dev")
    granted = await _grant(
        client,
        {"subject_type": "user", "subject": ids["user editor grant"], "role": "editor"},
        {"subject_type": "user", "subject": ids["user viewer grant"], "role": "viewer"},
        {"subject_type": "user", "subject": ids["user owner grant"], "role": "owner"},
        {"subject_type": "group", "subject": "writers", "role": "editor"},
    )
    assert granted.status_code == 200, granted.text
    if who == "admin":
        watcher = client
        observed = await _outcomes(watcher)
        shown = await client.get(f"{ORG}/fiches")
        # archived by the matrix itself: read it before, below
        assert observed == EXPECTED[who]
        assert shown.status_code == 200
        return
    async with _as(client, _email(who), **PEOPLE[who]) as c:
        shown = await c.get(f"{ORG}/fiches")
        observed = await _outcomes(c)
    assert observed == EXPECTED[who], who
    if shown.status_code != 200:
        assert not any(observed.values()), "a base the caller cannot read is 404 for every gesture"
        return
    can, reason = shown.json()["can"], shown.json()["cannot_reason"]
    assert (can["edit"], can["distill"], can["share"], can["widen"], can["archive"]) == (
        observed["upload"],
        observed["distill"],
        observed["share"],
        observed["widen"],
        observed["archive"],
    ), "`can` says what the routes enforce"
    assert (reason is None) == can["edit"], "the server's sentence appears exactly when edit is refused"
    assert (shown.json()["grants"] is not None) == can["share"], (
        "who holds what is shown to who may change it"
    )


async def test_the_admin_dto_says_everything_is_allowed(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    body = (await client.get(f"{ORG}/fiches")).json()
    assert body["can"] == {"edit": True, "distill": True, "share": True, "archive": True, "widen": True}
    assert body["my_role"] == "owner" and body["cannot_reason"] is None and body["grants"] == []


async def test_a_stranger_to_the_organisation_reads_nothing(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    async with _as(client, "stranger@elsewhere.dev") as stranger:
        for call in (
            stranger.get(ORG),
            stranger.get(f"{ORG}/fiches"),
            stranger.get(f"{ORG}/fiches/grants"),
            stranger.put(f"{ORG}/fiches/grants", json={"grants": []}),
            stranger.post(f"{ORG}/fiches/sync"),
        ):
            assert (await call).status_code in {403, 404}


async def test_a_grant_by_e_mail_does_not_cross_organisations(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    """A grant on another organisation's base, even naming this person's e-mail, gives them nothing."""
    from choregos_api.db.models import KnowledgeBase, Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        other = Organization(slug="other", name="Other")
        session.add(other)
        await session.flush()
        session.add(KnowledgeBase(org_id=other.id, slug="secret", name="Secret", scope="org"))
    await login(client, "admin@varga.dev")
    async with _as(client, "admin@other.dev") as other_admin:  # lands in no organisation of its own
        pass
    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as person:
        for path in ("/api/v1/orgs/other/knowledge/secret", "/api/v1/orgs/other/knowledge/secret/grants"):
            assert (await person.get(path)).status_code in {403, 404}
        assert (
            await person.put("/api/v1/orgs/other/knowledge/secret/grants", json={"grants": []})
        ).status_code in {403, 404}
    # even when an administrator of `other` names this person's e-mail, it is not enough
    from choregos_api.db.models import KnowledgeGrant

    async with session_scope() as session:
        base = (await session.execute(__import__("sqlalchemy").select(KnowledgeBase))).scalars().all()
        secret = next(b for b in base if b.slug == "secret")
        session.add(
            KnowledgeGrant(
                org_id=secret.org_id,
                knowledge_base_id=secret.id,
                subject_type="user",
                subject=_email("project owner"),
                role="owner",
            )
        )
    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as person:
        assert (await person.get("/api/v1/orgs/other/knowledge/secret")).status_code in {403, 404}
        assert (await person.post("/api/v1/orgs/other/knowledge/secret/sync")).status_code in {403, 404}
    del other_admin


async def test_a_project_owner_syncs_the_whole_base(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    """Gap 5 of the plan: the route required `agent:manage` while every source route took the owners."""
    await _admin_base(client, two_projects)
    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as owner:
        assert (await owner.post(f"{ORG}/fiches/sources", json=SOURCE)).status_code == 201
        synced = await owner.post(f"{ORG}/fiches/sync")
        assert synced.status_code == 202, synced.text
    async with _as(client, _email("project developer"), **PEOPLE["project developer"]) as dev:
        assert (await dev.post(f"{ORG}/fiches/sync")).status_code == 403


async def test_a_project_grant_gives_its_members_the_role_and_other_projects_nothing(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    put = await _grant(client, {"subject_type": "project", "subject": "checkout-web", "role": "editor"})
    assert put.status_code == 200, put.text
    async with _as(client, "member@varga.dev", projects={"checkout-web": "viewer"}) as member:
        assert (await member.get(f"{ORG}/fiches")).status_code == 200
        assert _ok(await member.post(f"{ORG}/fiches/documents", files={"file": ("n.md", b"# N\n")}))
        assert (await member.put(f"{ORG}/fiches/grants", json={"grants": []})).status_code == 403
    async with _as(client, "wide@varga.dev", org_role="viewer") as wide:
        # an organisation-wide role is not membership of checkout-web: it reads billing-api's base
        # through the reach, and the project grant does not make it an editor
        assert (await wide.get(f"{ORG}/fiches")).status_code == 200
        assert (
            await wide.post(f"{ORG}/fiches/documents", files={"file": ("m.md", b"# M\n")})
        ).status_code == 403


async def test_restricted_base_hides_from_project_viewers_but_serves_runs(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    from .test_knowledge_files import _pinned, _run

    created = await client.post(
        ORG, json={"slug": "architecture", "name": "Architecture", "restricted": True}
    )
    assert created.status_code == 201 and created.json()["restricted"] is True
    viewer_id = await _uid(client, "reader@varga.dev", org_role="viewer")
    await login(client, "admin@varga.dev")
    async with _as(client, "reader@varga.dev", org_role="viewer") as reader:
        assert (await reader.get(f"{ORG}/architecture")).status_code == 404
        assert [b["slug"] for b in (await reader.get(ORG)).json()] == []
    granted = await client.put(
        f"{ORG}/architecture/grants",
        json={"grants": [{"subject_type": "user", "subject": viewer_id, "role": "viewer"}]},
    )
    assert granted.status_code == 200, granted.text
    async with _as(client, "reader@varga.dev", org_role="viewer") as reader:
        assert (await reader.get(f"{ORG}/architecture")).status_code == 200
        assert [b["slug"] for b in (await reader.get(ORG)).json()] == ["architecture"]
    # runs of any project are still served it
    from choregos_api.knowledge.access import served_to_project

    pinned = await _pinned(two_projects)
    assert pinned and pinned[0]["slug"] == "architecture"
    token = await _run(two_projects, pinned)
    client.cookies.clear()
    served = await client.get(
        "/api/v1/internal/runs/run-kb/knowledge", headers={"Authorization": f"Bearer {token}"}
    )
    assert served.status_code == 200, served.text
    assert served_to_project(SimpleNamespace(status="active", scope="org", projects=[], restricted=True), "x")


async def test_a_grant_never_serves_a_run(client: AsyncClient, two_projects: dict[str, Any]) -> None:
    from choregos_api.knowledge.serving import KnowledgeRefusedError

    from .test_knowledge_files import _pinned

    created = await client.post(
        ORG, json={"slug": "architecture", "name": "A", "scope": "projects", "projects": ["checkout-web"]}
    )
    assert created.status_code == 201, created.text
    granted = await _grant(
        client,
        {"subject_type": "project", "subject": "billing-api", "role": "editor"},
        slug="architecture",
    )
    assert granted.status_code == 200, granted.text
    with pytest.raises(KnowledgeRefusedError):
        await _pinned(two_projects)  # billing-api: holds a role, is not in the reach


async def test_widening_still_clears_owner_project_and_restricting_does_not(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as owner:
        assert (await owner.patch(f"{ORG}/fiches", json={"restricted": True})).status_code == 403
    await login(client, "admin@varga.dev")
    restricted = await client.patch(f"{ORG}/fiches", json={"restricted": True})
    assert restricted.status_code == 200 and restricted.json()["owner_project"] == "billing-api"
    assert restricted.json()["restricted"] is True
    widened = await client.patch(f"{ORG}/fiches", json={"scope": "org", "restricted": False})
    assert widened.status_code == 200
    assert widened.json()["owner_project"] is None and widened.json()["restricted"] is False


async def test_the_owner_role_cannot_widen_but_administers_the_rest(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    uid = await _uid(client, "boss@varga.dev", projects={"checkout-web": "viewer"})
    await login(client, "admin@varga.dev")
    await _grant(client, {"subject_type": "user", "subject": uid, "role": "owner"})
    async with _as(client, "boss@varga.dev", projects={"checkout-web": "viewer"}) as boss:
        for widening in (
            {"scope": "org"},
            {"projects": ["billing-api", "checkout-web"]},
            {"restricted": True},
        ):
            assert (await boss.patch(f"{ORG}/fiches", json=widening)).status_code == 403, widening
        assert (await boss.patch(f"{ORG}/fiches", json={"name": "Renamed"})).status_code == 200
        assert (await boss.patch(f"{ORG}/fiches", json={"status": "archived"})).status_code == 200


async def test_an_editor_cannot_archive_but_can_rename(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    await _admin_base(client, two_projects)
    uid = await _uid(client, "ed@varga.dev", projects={"checkout-web": "viewer"})
    await login(client, "admin@varga.dev")
    await _grant(client, {"subject_type": "user", "subject": uid, "role": "editor"})
    async with _as(client, "ed@varga.dev", projects={"checkout-web": "viewer"}) as editor:
        assert (await editor.patch(f"{ORG}/fiches", json={"name": "Renamed"})).status_code == 200
        assert (await editor.patch(f"{ORG}/fiches", json={"serving_mode": "index"})).status_code == 200
        assert (await editor.patch(f"{ORG}/fiches", json={"status": "archived"})).status_code == 403


async def test_grants_put_is_idempotent_and_audited(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _admin_base(client, two_projects)
    wanted = [
        {"subject_type": "group", "subject": "writers", "role": "editor"},
        {"subject_type": "project", "subject": "checkout-web", "role": "viewer"},
    ]

    async def audits() -> list[AuditLog]:
        async with session_scope() as session:
            return list(
                (await session.execute(select(AuditLog).where(AuditLog.action == "knowledge.grant.set")))
                .scalars()
                .all()
            )

    first = await _grant(client, *wanted)
    assert first.status_code == 200, first.text
    again = await _grant(client, *wanted)
    assert again.status_code == 200
    assert [(g["subject_type"], g["subject"], g["role"]) for g in again.json()["grants"]] == [
        (g["subject_type"], g["subject"], g["role"]) for g in first.json()["grants"]
    ]
    assert len(await audits()) == 1, "the same list twice changes nothing and leaves one event"
    changed = await _grant(client, {"subject_type": "group", "subject": "writers", "role": "viewer"})
    assert changed.status_code == 200 and len(changed.json()["grants"]) == 1
    events = await audits()
    assert len(events) == 2
    last = events[-1].payload
    assert last["removed"] == ["project:checkout-web"] and last["changed"] == ["group:writers=viewer"]
    assert events[0].payload["added"] == ["group:writers=editor", "project:checkout-web=viewer"]
    assert (await client.get(f"{ORG}/fiches")).json()["grants"][0]["created_by"] == "admin@varga.dev"


async def test_unknown_member_or_project_is_422(client: AsyncClient, two_projects: dict[str, Any]) -> None:
    await _admin_base(client, two_projects)
    for grant in (
        {"subject_type": "user", "subject": "no-such-member-id", "role": "viewer"},
        {"subject_type": "user", "subject": "not an address@", "role": "viewer"},
        {"subject_type": "project", "subject": "no-such-project", "role": "viewer"},
        {"subject_type": "group", "subject": "  ", "role": "viewer"},
        {"subject_type": "group", "subject": "writers", "role": "boss"},
    ):
        refused = await _grant(client, grant)
        assert refused.status_code == 422, (grant, refused.text)
    twice = await _grant(
        client,
        {"subject_type": "group", "subject": "writers", "role": "viewer"},
        {"subject_type": "group", "subject": "writers", "role": "editor"},
    )
    assert twice.status_code == 422
    assert (await client.get(f"{ORG}/fiches/grants")).json() == {"grants": []}, (
        "a refused list changes nothing"
    )
    too_many = [{"subject_type": "group", "subject": f"g{i}", "role": "viewer"} for i in range(201)]
    assert (await _grant(client, *too_many)).status_code == 422


async def test_a_grant_by_e_mail_waits_for_a_person_invited_later(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    created = await client.post(ORG, json={"slug": "figures", "name": "Figures", "restricted": True})
    assert created.status_code == 201
    put = await _grant(
        client, {"subject_type": "user", "subject": "Later@Varga.dev", "role": "editor"}, slug="figures"
    )
    assert put.status_code == 200, put.text
    (waiting,) = put.json()["grants"]
    assert waiting["subject"] == "later@varga.dev" and waiting["pending"] is True
    async with _as(client, "later@varga.dev") as later:
        # signed in, but the development sign-in made them an organisation member: restore "no role"
        pass
    async with _as(client, "later@varga.dev", org_role="viewer") as later:
        assert (await later.get(f"{ORG}/figures")).status_code == 200, "the e-mail resolves at membership"
        assert _ok(await later.post(f"{ORG}/figures/documents", files={"file": ("n.md", b"# N\n")}))
    listed = await client.get(f"{ORG}/figures/grants")
    assert listed.json()["grants"][0]["pending"] is False, "no longer waiting once the person is a member"
    # an e-mail that belongs to a member is stored as that member's id
    again = await _grant(
        client, {"subject_type": "user", "subject": "LATER@varga.dev", "role": "viewer"}, slug="figures"
    )
    assert again.json()["grants"][0]["subject"] != "later@varga.dev"
    assert again.json()["grants"][0]["display"]


async def test_list_does_not_query_grants_per_base(client: AsyncClient, two_projects: dict[str, Any]) -> None:
    from choregos_api.db.session import get_engine
    from sqlalchemy import event

    for index in range(5):
        created = await client.post(ORG, json={"slug": f"base-{index}", "name": f"B{index}"})
        assert created.status_code == 201
    seen: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if "knowledge_grants" in statement:
            seen.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        listed = await client.get(ORG)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(listed.json()) == 5
    assert len(seen) == 1, f"one query for the grants of every base, saw {len(seen)}"


async def test_a_deleted_base_takes_its_grants(client: AsyncClient, two_projects: dict[str, Any]) -> None:
    from choregos_api.db.models import KnowledgeGrant
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    await _admin_base(client, two_projects)
    await _grant(client, {"subject_type": "group", "subject": "writers", "role": "viewer"})
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(KnowledgeGrant))).scalar_one() == 1


async def test_the_agent_drafter_suggests_only_what_the_caller_reads(
    client: AsyncClient, two_projects: dict[str, Any]
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.agent_drafts import context_of
    from sqlalchemy import select

    await client.post(ORG, json={"slug": "everyone", "name": "Everyone"})
    await client.post(ORG, json={"slug": "private-figures", "name": "Private", "restricted": True})
    await client.post(ORG, json={"slug": "granted", "name": "Granted", "restricted": True})
    await _grant(client, {"subject_type": "group", "subject": "writers", "role": "viewer"}, slug="granted")
    plain = Principal(user_id="u1", email="p@varga.dev", org_roles={"varga": Role.VIEWER})
    writer = Principal(
        user_id="u2", email="w@varga.dev", org_roles={"varga": Role.VIEWER}, groups=["writers"]
    )
    async with session_scope() as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one()
        seen = {
            who: [slug for slug, _ in (await context_of(session, organisation, p)).knowledge]
            for who, p in (("plain", plain), ("writer", writer))
        }
    assert seen["plain"] == ["everyone"], "a base the caller cannot read is not even named"
    assert seen["writer"] == ["everyone", "granted"]


def test_decide_is_one_function() -> None:
    from choregos_api.knowledge.access import KnowledgeAction, decide, role_of, visible_to
    from choregos_api.rbac import Principal, Role

    base = SimpleNamespace(
        scope="org", projects=[], groups=[], owner_project=None, restricted=False, status="active"
    )
    grant = SimpleNamespace(subject_type="user", subject="Ana@x.dev", role="editor")
    ana = Principal(user_id="u", email="ana@x.dev", org_roles={"varga": Role.VIEWER})
    assert role_of(ana, "varga", base, [grant]) == "editor", "an e-mail grant matches case-insensitively"
    assert decide(ana, "varga", base, KnowledgeAction.EDIT, [grant]).allowed
    refused = decide(ana, "varga", base, KnowledgeAction.ARCHIVE, [grant])
    assert not refused.allowed and "owner" in (refused.reason or "")
    assert not decide(ana, "varga", base, KnowledgeAction.EDIT).allowed
    stranger = Principal(user_id="s", email="ana@x.dev", org_roles={"elsewhere": Role.ORG_ADMIN})
    assert not visible_to(stranger, "varga", base, [grant]), "a grant never crosses organisations"
    assert role_of(stranger, "varga", base, [grant]) is None
    restricted = SimpleNamespace(**{**vars(base), "restricted": True})
    reader = Principal(user_id="r", email="r@x.dev", org_roles={"varga": Role.VIEWER})
    assert not visible_to(reader, "varga", restricted)
    assert visible_to(
        reader, "varga", restricted, [SimpleNamespace(subject_type="user", subject="r", role="viewer")]
    )
