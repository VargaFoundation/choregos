"""Which edition is running, and what does it refuse?

The community edition is **single-organisation** ([ADR 0024](../../../docs/adr/0024-two-editions.md)).
It is not an arbitrary cap: multi-tenancy is not finished here (thirteen tables are still outside
RLS), and an installation with a single organisation is exposed to none of those leaks, because
there is nothing to cross.

Three properties, and the second is the real one: the refusal must not depend on a hard-coded cap
but on the edition, so that the enterprise edition lifts it without touching the core.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


@pytest.fixture(autouse=True)
def default_edition() -> Any:
    """Each test starts from the most restrictive edition."""
    from choregos_api import edition

    edition.reset()
    yield
    edition.reset()


async def test_the_edition_can_be_asked(client: AsyncClient) -> None:
    """Without a route, the only way to know would be to try an action and read the refusal."""
    response = await client.get("/edition")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["edition"] == "community"
    assert body["features"] == []
    assert body["version"] and body["version"] != "1.0.0", "the version must no longer be hard-coded"


async def test_the_edition_can_be_asked_under_the_contract_prefix(client: AsyncClient) -> None:
    """The contract puts it under `/api/v1`: that is where the console asks for it.

    It was only served at the root, which the Ingress sends to the console, so the console could
    not say which edition it ran on, and showed nothing of the enterprise edition.
    """
    under_the_prefix = await client.get("/api/v1/edition")
    assert under_the_prefix.status_code == 200, under_the_prefix.text
    assert under_the_prefix.json() == (await client.get("/edition")).json()


async def test_the_community_edition_refuses_a_second_organisation(
    client: AsyncClient, org: str, admin: str
) -> None:
    response = await client.post("/api/v1/orgs", json={"slug": "second", "name": "Second"})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "community" in detail and "0024" in detail, "the refusal must name the decision"


async def test_the_enterprise_edition_lets_it_through(client: AsyncClient, org: str, admin: str) -> None:
    """The refusal is about the EDITION, not a hard-coded cap: otherwise the enterprise edition
    would have to fork the core to lift it, which ADR 0024 explicitly refuses."""
    from choregos_api import edition

    edition.declare(edition.ENTERPRISE, features=frozenset({"multi-org"}))
    response = await client.post("/api/v1/orgs", json={"slug": "second", "name": "Second"})
    assert response.status_code == 201, response.text

    announced = (await client.get("/edition")).json()
    assert announced["edition"] == "enterprise"
    assert announced["features"] == ["multi-org"]


async def test_without_an_organisation_the_edition_is_not_what_refuses(client: AsyncClient) -> None:
    """The very first organisation comes from the bootstrap (`CHOREGOS_BOOTSTRAP_ORG`), not from
    this route: creating an organisation requires already being an administrator of one.

    What this test holds is that the edition guard does not get in the way of that path: on an
    empty installation, the refusal stays a **403 about rights**, not a 409 about the edition.
    Otherwise the community edition would not be single-organisation, it would be
    zero-organisation.
    """
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    await login(client, "admin@varga.dev")
    async with session_scope(orgs="*") as session:
        await session.execute(delete(Organization))

    response = await client.post("/api/v1/orgs", json={"slug": "first", "name": "First"})
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert "platform" in detail, f"the refusal must talk about rights, not the edition: {detail}"
    assert "community" not in detail, "it is not the edition that refuses here"


async def test_administering_the_platform_is_a_right_on_the_instance(
    client: AsyncClient, org: str, admin: str
) -> None:
    """The defect logged in BLOCKERS on 2026-09-26, and fixed here.

    `Principal.is_platform_admin()` returned true as soon as one was `org_admin` of ANY
    organisation. On an installation with a single organisation that is exact: the administrator
    of that organisation IS the instance's. As soon as there are two, it no longer is, and the
    predicate granted the `/platform/*` routes (backends, executors, model profiles, gateway keys)
    to the administrator of any tenant.

    It now requires administering ALL the organisations. In multi-org, that is true of nobody until
    the enterprise edition has defined a real platform role: a refusal, and that is the right
    default: better a route nobody can reach than a route open to the wrong tenant.
    """
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    # `GET /platform/gateway/keys`: budgets, spend, expiry, revocation. The most sensitive route
    # of `/platform/*`, and one of the three that were guarded.
    # (In passing: `GET /platform/backends` and `GET /platform/executors` have NO guard; only the
    # `PUT`s had one. It is not a secret, but it is inconsistent. Noted.)
    assert (await client.get("/api/v1/platform/gateway/keys")).status_code == 200

    async with session_scope(orgs="*") as session:
        session.add(Organization(slug="other-tenant", name="Other"))

    refusal = await client.get("/api/v1/platform/gateway/keys")
    assert refusal.status_code == 403, refusal.text
    detail = refusal.json()["detail"]
    assert "instance" in detail, f"the refusal must say why: {detail}"
    assert "other-tenant" in detail, "and name what is missing, otherwise nobody understands"


def test_the_edition_names_its_publisher_and_version() -> None:
    from choregos_api import edition

    edition.declare(edition.ENTERPRISE, publisher="  Acme Corp ", version="1.2.3")
    assert edition.publisher() == "Acme Corp"
    assert edition.edition_version() == "1.2.3"
    edition.reset()
    assert edition.publisher() is None
    assert edition.edition_version() is None


@pytest.mark.parametrize(
    ("publisher", "version"),
    [
        ("", None),
        ("   ", None),
        ("x" * 81, None),
        ("bad\nname", None),
        (None, "1 2"),
        (None, "v" * 41),
        (None, ""),
    ],
)
def test_a_malformed_publisher_or_version_is_refused(publisher: str | None, version: str | None) -> None:
    from choregos_api import edition

    with pytest.raises(ValueError):
        edition.declare(edition.ENTERPRISE, publisher=publisher, version=version)
    assert edition.publisher() is None


def test_the_community_edition_has_no_publisher() -> None:
    from choregos_api import edition

    with pytest.raises(ValueError, match="community"):
        edition.declare(edition.COMMUNITY, publisher="Acme")
    with pytest.raises(ValueError, match="community"):
        edition.declare(edition.COMMUNITY, version="1.0.0")


async def test_the_edition_route_serves_the_publisher(client: AsyncClient) -> None:
    from choregos_api import edition

    community = (await client.get("/edition")).json()
    assert community["publisher"] is None and community["edition_version"] is None

    edition.declare(edition.ENTERPRISE, features=frozenset({"saml"}), publisher="Acme", version="0.2.0")
    for path in ("/edition", "/api/v1/edition"):
        body = (await client.get(path)).json()
        assert body["edition"] == "enterprise"
        assert body["publisher"] == "Acme"
        assert body["edition_version"] == "0.2.0"
        assert body["version"] and body["version"] != "0.2.0", "the core's version stays the core's"
        import choregos_contracts as contracts
        from jsonschema import Draft202012Validator

        schema = contracts.load_openapi()["paths"]["/edition"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        Draft202012Validator(schema).validate(body)


def test_the_edition_names_its_licence() -> None:
    from choregos_api import edition

    assert edition.edition_licence() is None
    edition.declare(edition.ENTERPRISE, licence="  Acme Corp EULA ")
    assert edition.edition_licence() == "Acme Corp EULA"
    edition.reset()
    assert edition.edition_licence() is None


@pytest.mark.parametrize("licence", ["", "   ", "x" * 81, "bad\nlicence", "tab\there", "del\x7f"])
def test_a_malformed_licence_is_refused(licence: str) -> None:
    from choregos_api import edition

    with pytest.raises(ValueError, match="licence"):
        edition.declare(edition.ENTERPRISE, licence=licence)
    assert edition.edition_licence() is None


def test_the_community_edition_has_no_licence() -> None:
    from choregos_api import edition

    with pytest.raises(ValueError, match="community"):
        edition.declare(edition.COMMUNITY, licence="Acme Corp EULA")


def _validate_edition_body(body: dict[str, object]) -> None:
    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    schema = contracts.load_openapi()["paths"]["/edition"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator(schema).validate(body)


async def test_the_edition_route_serves_the_licences_and_no_revision_by_default(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import edition

    monkeypatch.delenv("CHOREGOS_BUILD_REVISION", raising=False)
    community = (await client.get("/api/v1/edition")).json()
    assert community["licence"] == "Apache-2.0"
    assert community["edition_licence"] is None
    assert community["build"] == {"revision": None}
    _validate_edition_body(community)

    edition.declare(edition.ENTERPRISE, publisher="Acme Corp", version="0.2.0", licence="Acme Corp EULA")
    for path in ("/edition", "/api/v1/edition"):
        body = (await client.get(path)).json()
        assert body["licence"] == "Apache-2.0", "the core's licence stays the core's"
        assert body["edition_licence"] == "Acme Corp EULA"
        _validate_edition_body(body)


@pytest.mark.parametrize(
    ("raw", "served"),
    [
        ("0123abc", "0123abc"),
        ("  " + "a" * 40 + "\n", "a" * 40),
        ("", None),
        ("abc", None),
        ("a" * 41, None),
        ("ABCDEF0", None),
        ("not a revision", None),
        ("<script>alert(1)</script>", None),
    ],
)
async def test_the_edition_route_serves_the_build_revision_only_when_it_is_a_commit_id(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, raw: str, served: str | None
) -> None:
    monkeypatch.setenv("CHOREGOS_BUILD_REVISION", raw)
    for path in ("/edition", "/api/v1/edition"):
        body = (await client.get(path)).json()
        assert body["build"] == {"revision": served}
        _validate_edition_body(body)
