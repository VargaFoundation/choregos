"""The administration sections declared by an extension (ADR 0032, S17-01).

An extension (the enterprise edition) serves its administration routes; it declares its screens as
DATA, and the console renders them. The core checks at start-up that each section is well formed
and that each of its paths is served, and shows a section only to whoever has its right.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import choregos_contracts as contracts
import pytest
from fastapi import APIRouter
from httpx import AsyncClient

from .conftest import login

SCIM: dict[str, Any] = json.loads(
    (contracts.schemas_dir() / "examples" / "ui-manifest.example.json").read_text()
)
#: The start-up messages the core raises are French; the tests pin them as they are.
INVALID = "invalide"  # lang: fr-ok
UNKNOWN_PERMISSION = "permission inconnue"  # lang: fr-ok
NO_ROUTE = "aucune route"  # lang: fr-ok
DECLARED_TWICE = "deux fois"  # lang: fr-ok


def _scim_router() -> APIRouter:
    """The routes the example manifest names, served by the "extension"."""
    router = APIRouter()

    @router.get("/orgs/{org}/scim/settings")
    async def read(org: str) -> dict[str, Any]:
        return {"enabled": False}

    @router.put("/orgs/{org}/scim/settings")
    async def write(org: str) -> dict[str, Any]:
        return {"enabled": True}

    @router.post("/orgs/{org}/scim/badge")
    async def upload(org: str) -> dict[str, Any]:
        return {"ref": "sha256-0"}

    @router.get("/orgs/{org}/scim/tokens")
    async def list_tokens(org: str) -> dict[str, Any]:
        return {"items": []}

    @router.post("/orgs/{org}/scim/tokens")
    async def create(org: str) -> dict[str, Any]:
        return {"id": "t1", "token": "scim_secret"}

    @router.delete("/orgs/{org}/scim/tokens/{token_id}")
    async def revoke(org: str, token_id: str) -> dict[str, Any]:
        return {}

    return router


@pytest.fixture
def extension() -> Iterator[Any]:
    from choregos_api import extensions

    extensions.declare_router(_scim_router())
    extensions.declare_admin_section(SCIM)
    platform_section = {
        "id": "organisations",
        "title": "organisations",
        "scope": "platform",
        "permission": "platform:admin",
        "blocks": [
            {
                "kind": "table",
                "title": "organisations",
                "list": "/orgs",
                "columns": [{"key": "slug", "label": "slug"}],
            }
        ],
    }
    extensions.declare_admin_section(platform_section)
    yield extensions
    extensions.reset()


async def test_the_administrator_sees_the_sections_the_developer_does_not(
    extension: Any, client: AsyncClient, admin: str
) -> None:
    sections = (await client.get("/api/v1/ui/admin-sections")).json()
    assert [s["id"] for s in sections] == ["scim", "organisations"]
    assert sections[0]["blocks"][2]["secret_field"] == "token"

    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert member.status_code in {200, 201}, member.text
    await login(client, "dev@varga.dev")
    assert (await client.get("/api/v1/ui/admin-sections")).json() == [], (
        "neither member:manage nor the platform"
    )


@pytest.mark.parametrize(
    ("alteration", "reason"),
    [
        pytest.param(lambda m: m.pop("blocks"), INVALID, id="invalid-manifest"),
        pytest.param(
            lambda m: m.update(permission="org:everything"), UNKNOWN_PERMISSION, id="unknown-permission"
        ),
        pytest.param(
            lambda m: m["blocks"][0].update(permission="org:everything"),
            "unknown block permission",
            id="block-permission-unknown",
        ),
        pytest.param(
            lambda m: m["blocks"][1].update(list="/orgs/{org}/scim/missing"),
            NO_ROUTE,
            id="path-not-served",
        ),
        pytest.param(
            lambda m: m["blocks"][0]["schema"]["properties"]["badge"].update(
                **{"x-upload": "/orgs/{org}/scim/nope"}
            ),
            NO_ROUTE,
            id="asset-upload-path-not-served",
        ),
        pytest.param(
            lambda m: m["blocks"][0]["schema"]["properties"]["badge"].pop("x-upload"),
            INVALID,
            id="asset-without-upload-path",
        ),
        pytest.param(
            lambda m: m["blocks"][1]["row_actions"][0].update(method="POST"),
            NO_ROUTE,
            id="method-not-served",
        ),
        pytest.param(lambda m: m.update(icon="rocket"), INVALID, id="icon-outside-the-vocabulary"),
    ],
)
def test_a_section_that_does_not_hold_stops_the_start_up(alteration: Any, reason: str) -> None:
    from choregos_api import extensions
    from choregos_api.main import create_app

    faulty = copy.deepcopy(SCIM)
    alteration(faulty)
    extensions.declare_router(_scim_router())
    extensions.declare_admin_section(faulty)
    try:
        with pytest.raises(RuntimeError, match=reason):
            create_app()
    finally:
        extensions.reset()


def test_the_same_extension_loaded_twice_declares_only_one_section() -> None:
    """The orchestrator the API imports loads the extensions, then `create_app()` reloads them: the
    same section comes back, and must not stop the start-up. A DIFFERENT one under the same name does."""
    from choregos_api import extensions

    try:
        extensions.declare_admin_section(SCIM)
        extensions.declare_admin_section(copy.deepcopy(SCIM))
        assert [s["id"] for s in extensions.declared_sections()] == ["scim"]
        different = copy.deepcopy(SCIM)
        different["title"] = "another section"
        with pytest.raises(ValueError, match=DECLARED_TWICE):
            extensions.declare_admin_section(different)
    finally:
        extensions.reset()


async def test_an_organisation_section_that_asks_for_the_platform_shows_only_to_it(
    extension: Any, client: AsyncClient, admin: str
) -> None:
    """`org_admin` has every permission in its organisation, `platform:admin` included: without a
    guard, the section of the ceilings of an organisation, which only the platform can write, was
    shown to the administrator of any tenant, and every save answered 403."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    extension.declare_admin_section(
        {
            "id": "limits",
            "title": "limits",
            "scope": "organisation",
            "permission": "platform:admin",
            "blocks": [
                {"kind": "table", "title": "t", "list": "/orgs", "columns": [{"key": "slug", "label": "s"}]}
            ],
        }
    )
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert ids == ["scim", "organisations", "limits"], (
        "a single organisation: its administrator has the instance"
    )

    # A second organisation: the administrator of `varga` no longer administers the instance.
    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert ids == ["scim"], "member:manage in its organisation, nothing of the platform"


async def test_a_block_the_caller_lacks_is_filtered_and_the_section_stays(
    extension: Any, client: AsyncClient, admin: str
) -> None:
    """S42-02: the example's last block needs `platform:admin`. With two organisations the
    organisation administrator keeps the section and its other blocks, not that block."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    first = (await client.get("/api/v1/ui/admin-sections")).json()[0]
    assert [b["kind"] for b in first["blocks"]] == ["form", "table", "secret_once"]

    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    [scim] = (await client.get("/api/v1/ui/admin-sections")).json()
    assert scim["id"] == "scim"
    assert [b["kind"] for b in scim["blocks"]] == ["form", "table"]
    assert scim["blocks"][1]["row_actions"][0]["method"] == "DELETE", "nested row_actions untouched"


async def test_a_section_with_no_visible_block_is_not_listed(
    extension: Any, client: AsyncClient, admin: str
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    extension.declare_admin_section(
        {
            "id": "acme-instance",
            "title": "Appearance",
            "scope": "organisation",
            "permission": "member:manage",
            "blocks": [
                {
                    "kind": "table",
                    "title": "instance",
                    "permission": "platform:admin",
                    "list": "/orgs",
                    "columns": [{"key": "slug", "label": "s"}],
                }
            ],
        }
    )
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert "acme-instance" in ids
    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert "acme-instance" not in ids


def test_the_core_announces_block_permission() -> None:
    from choregos_api.extensions import SECTION_FEATURES

    assert "block_permission" in SECTION_FEATURES


def test_the_core_announces_asset_upload_and_a_section_lists_its_upload_paths() -> None:
    """S42-13: the paths a section calls include the POST path of each `format: asset` property."""
    from choregos_api.extensions import SECTION_FEATURES, section_paths

    assert "asset_upload" in SECTION_FEATURES
    paths = section_paths(SCIM)
    assert ("POST", "/orgs/{org}/scim/badge") in paths
    # Only `format: asset` properties count: a plain string with an `x-upload` key does not.
    plain = copy.deepcopy(SCIM)
    plain["blocks"][0]["schema"]["properties"]["badge"].pop("format")
    assert ("POST", "/orgs/{org}/scim/badge") not in section_paths(plain)


def test_the_core_announces_section_icon_and_accepts_the_vocabulary_only() -> None:
    """S42-15: a section names its navigation icon from the schema's closed vocabulary."""
    from choregos_api.extensions import SECTION_FEATURES
    from jsonschema import Draft202012Validator

    assert "section_icon" in SECTION_FEATURES
    schema = contracts.load_schema("ui-manifest.schema.json")
    validator = Draft202012Validator(schema)
    for icon in schema["properties"]["icon"]["enum"]:
        assert not list(validator.iter_errors({**SCIM, "icon": icon})), icon
    assert list(validator.iter_errors({**SCIM, "icon": "https://example.com/logo.svg"}))


@pytest.fixture
def extension_with_icon() -> Iterator[Any]:
    from choregos_api import extensions

    extensions.declare_router(_scim_router())
    extensions.declare_admin_section({**SCIM, "icon": "fingerprint"})
    yield extensions
    extensions.reset()


async def test_a_section_s_icon_is_served_as_declared(
    extension_with_icon: Any, client: AsyncClient, admin: str
) -> None:
    sections = (await client.get("/api/v1/ui/admin-sections")).json()
    assert [(s["id"], s.get("icon")) for s in sections] == [("scim", "fingerprint")]
