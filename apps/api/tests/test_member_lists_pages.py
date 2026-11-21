"""The members, a member's linked accounts and the remote credentials are paged in SQL (ADR 0206).

`GET /orgs/{org}/members` (ordered by e-mail), `…/members/{user_id}/linked-accounts` (by provider, then
instance) and `GET /orgs/{org}/remote-credentials` (by name) read one page from the database. A name-ordered
list pages ascending, on a string key, and the keyset takes several keys: both are what these tests pin.
`…/tracker-identities` is bounded by construction (one GitHub link per member) and stays a bare array.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

ORG = "/api/v1/orgs/northwind"


async def _pages(client: AsyncClient, url: str, key: str, **params: Any) -> tuple[list[str], int]:
    """Every row of a list, page after page; the ids seen and the number of requests it took."""
    seen: list[str] = []
    cursor: str | None = None
    requests = 0
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        requests += 1
        seen += [str(item[key]) for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen, requests
        cursor = body["meta"]["next_cursor"]


async def _people(count: int, *, project_slug: str | None = None) -> None:
    """`count` people with an org membership each, all created at one instant; the even ones also belong
    to the project."""
    from sqlalchemy import select
    from tranibor_api.db.models import Membership, Organization, Project, User
    from tranibor_api.db.session import session_scope

    instant = utcnow()
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        project_id = None
        if project_slug:
            project_id = (
                await session.execute(select(Project.id).where(Project.slug == project_slug))
            ).scalar_one()
        for index in range(count):
            user = User(email=f"person-{index:03d}@northwind.dev", created_at=instant)
            session.add(user)
            await session.flush()
            session.add(Membership(user_id=user.id, org_id=org_id, role="viewer", created_at=instant))
            if project_id and index % 2 == 0:
                session.add(
                    Membership(
                        user_id=user.id,
                        org_id=org_id,
                        project_id=project_id,
                        role="developer",
                        created_at=instant,
                    )
                )


async def test_every_member_is_reachable_once_by_email(client: AsyncClient, project: dict[str, Any]) -> None:
    await _people(23, project_slug="billing-api")
    first = (await client.get(f"{ORG}/members", params={"limit": 10})).json()
    assert len(first["items"]) == 10 and first["meta"]["has_more"] is True
    # 23 org rows + 12 project rows + the administrator (and the project's creator rows, if any).
    rows, requests = await _pages(client, f"{ORG}/members", "email", limit=10)
    assert requests > 3
    everyone = (await client.get(f"{ORG}/members", params={"limit": 200})).json()["items"]
    assert len(rows) == len(everyone) and len(everyone) >= 35
    emails = [m["email"] for m in everyone]
    assert emails == sorted(emails), "ordered by e-mail, so one person's memberships are neighbours"
    seen = [(m["email"], m["project_slug"]) for m in everyone]
    assert len(set(seen)) == len(seen), "no row twice"
    paged: list[tuple[str, Any]] = []
    cursor: str | None = None
    while True:
        body = (
            await client.get(f"{ORG}/members", params={"limit": 7, **({"cursor": cursor} if cursor else {})})
        ).json()
        paged += [(m["email"], m["project_slug"]) for m in body["items"]]
        cursor = body["meta"]["next_cursor"]
        if not body["meta"]["has_more"]:
            break
    assert paged == seen, "pages of 7 and one page of 200 are the same list, row for row"


async def test_the_member_filters_run_in_the_query(client: AsyncClient, project: dict[str, Any]) -> None:
    await _people(12, project_slug="billing-api")
    of_the_project, _ = await _pages(client, f"{ORG}/members", "email", project="billing-api", limit=2)
    assert [e for e in of_the_project if e.startswith("person-")] == [
        f"person-{i:03d}@northwind.dev" for i in range(0, 12, 2)
    ]
    assert await _pages(client, f"{ORG}/members", "email", q="PERSON-007", limit=2) == (
        ["person-007@northwind.dev"],
        1,
    )
    by_text, _ = await _pages(client, f"{ORG}/members", "email", q="person-01", limit=1)
    assert by_text == ["person-010@northwind.dev"] * 2 + ["person-011@northwind.dev"], (
        "010 holds two memberships"
    )
    assert (await _pages(client, f"{ORG}/members", "email", q="%"))[0] == [], "LIKE wildcards are escaped"
    assert (await _pages(client, f"{ORG}/members", "email", q="person-004", project="billing-api"))[0] == [
        "person-004@northwind.dev"
    ]


async def test_a_project_reader_pages_only_the_projects_they_read(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The visibility rule moved into the query: a page is full of rows the reader may see, never a short
    # page that a Python filter emptied afterwards.
    from tranibor_api.config import reset_settings_cache

    from .conftest import login

    # The dev login makes a new user a developer of every organisation unless a default one is set.
    monkeypatch.setenv("TRANIBOR_OIDC_DEFAULT_ORG", "acme")
    reset_settings_cache()
    await _people(30, project_slug="billing-api")
    from sqlalchemy import select
    from tranibor_api.db.models import Membership, Organization, Project, User
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        project_id = (
            await session.execute(select(Project.id).where(Project.slug == "billing-api"))
        ).scalar_one()
        reader = User(email="dev@northwind.dev")
        session.add(reader)
        await session.flush()
        # A role on the project only: no organisation-wide row, so only its memberships are readable.
        session.add(Membership(user_id=reader.id, org_id=org_id, project_id=project_id, role="developer"))
    client.cookies.clear()
    await login(client, "dev@northwind.dev")
    first = (await client.get(f"{ORG}/members", params={"limit": 5})).json()
    assert len(first["items"]) == 5, "full page, not 5 rows minus the invisible ones"
    assert all(m["project_slug"] == "billing-api" for m in first["items"]), [
        (m["email"], m["project_slug"], m["role"]) for m in first["items"]
    ]
    rows, _ = await _pages(client, f"{ORG}/members", "email", limit=5)
    assert len(rows) == 15 + 1 + 1, "15 people, the reader, and the administrator who created the project"
    assert not any(e.startswith("person-001") for e in rows), "an organisation-only person is not shown"


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    from tranibor_api.config import reset_settings_cache

    yield
    reset_settings_cache()


async def test_a_foreign_cursor_is_refused(client: AsyncClient, admin: str) -> None:
    from tranibor_api.keyset import encode_cursor

    for url in (f"{ORG}/members", f"{ORG}/remote-credentials"):
        assert (await client.get(url, params={"cursor": "not-a-cursor"})).status_code == 422
        assert (await client.get(url, params={"limit": 201})).status_code == 422
        # A well-formed cursor of another shape: a timestamp where a string is sorted, or one key too few.
        assert (await client.get(url, params={"cursor": encode_cursor(utcnow(), "id")})).status_code == 422
        assert (await client.get(url, params={"cursor": encode_cursor("a")})).status_code == 422


def test_the_string_and_multi_key_cursor_round_trips() -> None:
    from tranibor_api.keyset import decode_cursor, encode_cursor

    instant = utcnow()
    assert decode_cursor(encode_cursor(instant, "id-1")) == (instant, "id-1")
    assert decode_cursor(encode_cursor("forgejo", "https://git.example", "id-1")) == (
        "forgejo",
        "https://git.example",
        "id-1",
    )
    assert decode_cursor(encode_cursor("2026-11-20T00:00:00+00:00")) == ("2026-11-20T00:00:00+00:00",), (
        "a string that looks like a timestamp stays a string"
    )


async def test_the_remote_credentials_page_by_name(client: AsyncClient, admin: str) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import Organization, RemoteCredential
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        for name in ["zeta", *(f"cred-{index:02d}" for index in range(17)), "Alpha"]:
            session.add(RemoteCredential(org_id=org_id, name=name, type="none", config={}, status="pending"))
    names, requests = await _pages(client, f"{ORG}/remote-credentials", "name", limit=5)
    assert requests == 4 and len(names) == 19 and len(set(names)) == 19
    assert names == sorted(names), "ascending by name, as before"
    assert names[-1] == "zeta"


async def test_the_linked_accounts_page_by_provider_then_instance(client: AsyncClient, admin: str) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import LinkedAccount, Organization
    from tranibor_api.db.session import session_scope

    added = await client.post(f"{ORG}/members", json={"email": "dev@northwind.dev", "role": "developer"})
    dev = str(added.json()["user_id"])
    start = utcnow()
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        index = 0
        for provider in ("gitlab", "github", "forgejo"):
            for host in ("c", "a", "b", "d"):
                index += 1
                session.add(
                    LinkedAccount(
                        org_id=org_id,
                        user_id=dev,
                        provider=provider,
                        base_url=f"https://{host}.{provider}.example",
                        external_id=str(index),
                        login=f"dev-{index}",
                        created_at=start - timedelta(seconds=index),
                    )
                )
    url = f"{ORG}/members/{dev}/linked-accounts"
    rows, requests = await _pages(client, url, "login", limit=5)
    assert requests == 3 and len(set(rows)) == 12
    body = (await client.get(url, params={"limit": 200})).json()["items"]
    keys = [(a["provider"], a["base_url"]) for a in body]
    assert keys == sorted(keys), "by provider, then instance"
    assert [a["login"] for a in body] == rows, "five at a time or all at once: one order"


async def test_the_tracker_identities_stay_a_single_row_whatever_the_member_links(
    client: AsyncClient, admin: str
) -> None:
    """The reason the route is in BOUNDED: one GitHub link per member (`uq_linked_accounts_member`)."""
    from sqlalchemy import select
    from tranibor_api.db.models import LinkedAccount, Organization
    from tranibor_api.db.session import session_scope

    added = await client.post(f"{ORG}/members", json={"email": "dev@northwind.dev", "role": "developer"})
    dev = str(added.json()["user_id"])
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "northwind"))
        ).scalar_one()
        for index, (provider, base) in enumerate(
            [
                ("github", "https://github.com"),
                ("gitlab", "https://gitlab.com"),
                ("github", "https://ghe.example"),
            ]
        ):
            session.add(
                LinkedAccount(
                    org_id=org_id,
                    user_id=dev,
                    provider=provider,
                    base_url=base,
                    external_id=str(index),
                    login=f"d{index}",
                )
            )
    listed = (await client.get(f"{ORG}/members/{dev}/tracker-identities")).json()
    assert isinstance(listed, list) and len(listed) == 1 and listed[0]["external_id"] == "0"


@pytest.mark.parametrize("path", ["members", "remote-credentials"])
async def test_the_limit_is_honoured(client: AsyncClient, admin: str, path: str) -> None:
    body = (await client.get(f"{ORG}/{path}", params={"limit": 1})).json()
    assert len(body["items"]) <= 1 and set(body) == {"items", "meta"}
