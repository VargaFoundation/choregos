"""Connector secrets typed in the console: write-only, sealed at rest (S39-06c, ADR 0058).

A secret is typed once, stored sealed, replaced or cleared — and nothing the API answers, the audit
log records or `last_error` keeps ever holds the value. What leaves for the third-party server is
the CURRENT value, read with the organisation's context.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga/connectors"
VALUE_1 = "vendor-key-AAAA-1111"
VALUE_2 = "vendor-key-BBBB-2222"
TOOLS: list[dict[str, Any]] = [{"name": "order_status", "inputSchema": {"type": "object"}}]


@pytest.fixture(autouse=True)
def sealed_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """The deployment's key: without it nothing can be typed (ADR 0058)."""
    from choregos_core.redaction import forget_secrets

    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k1:{key}")
    forget_secrets()
    return key


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The vendor's MCP server, faked: it demands the connector's key as a bearer token."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(t) for t in TOOLS], jeton=VALUE_1)
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", server)
    yield server


def _declare(**extra: Any) -> dict[str, Any]:
    return {
        "name": "vendor",
        "type": "mcp",
        "config": {"url": "https://vendor.test/mcp"},
        **extra,
    }


async def _audit_text() -> str:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return json.dumps([(r.action, r.payload) for r in rows], default=str)


async def _stored_rows() -> list[Any]:
    from choregos_api.db.models import OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(OrgSecret))).scalars().all())


async def test_a_typed_secret_is_sealed_never_returned_and_never_audited(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    created = await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["secret_refs"] == {"token": "store:connector/vendor/token"}
    (state,) = body["secrets"]
    assert (state["field"], state["source"], state["version"]) == ("token", "store", 1)
    assert state["rotated_by"] == "admin@varga.dev" and state["rotated_at"]
    assert "reference" not in state or state["reference"] is None

    # nowhere in what the API returns...
    for route in (ORG, f"{ORG}/vendor"):
        assert VALUE_1 not in (await client.get(route)).text
    assert VALUE_1 not in created.text
    # ...nor in the audit log, which says who set which field at which version...
    audit = await _audit_text()
    assert VALUE_1 not in audit and "connector.secret.set" in audit and '"field": "token"' in audit
    # ...and the database holds an envelope, not the value.
    (row,) = await _stored_rows()
    assert row.name == "connector/vendor/token" and row.kek_id == "k1" and row.algorithm == "A256GCM"
    assert VALUE_1.encode() not in row.ciphertext + row.wrapped_dek + row.nonce


async def test_a_replaced_secret_is_the_one_the_vendor_receives(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    assert (await client.post(f"{ORG}/vendor/discover")).status_code == 200
    assert vendor.recues[-1][1]["authorization"] == f"Bearer {VALUE_1}"

    replaced = await client.put(f"{ORG}/vendor/secrets/token", json={"value": VALUE_2})
    assert replaced.status_code == 200, replaced.text
    (state,) = replaced.json()["secrets"]
    assert state["version"] == 2 and VALUE_2 not in replaced.text
    # The vendor still wants the old key: the NEW value is what went out, and it was refused.
    refused = await client.post(f"{ORG}/vendor/discover")
    assert refused.status_code == 502, refused.text
    assert vendor.recues[-1][1]["authorization"] == f"Bearer {VALUE_2}"
    assert VALUE_2 not in refused.text and VALUE_1 not in refused.text
    vendor.jeton = VALUE_2
    assert (await client.post(f"{ORG}/vendor/discover")).status_code == 200
    audit = await _audit_text()
    assert VALUE_2 not in audit and '"version": 2' in audit


async def test_a_store_reference_cannot_be_written_by_hand(client: AsyncClient, admin: str) -> None:
    """Else a connector could point at another connector's sealed secret and send it to a host the
    writer controls."""
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    other = {"name": "other", "type": "mcp", "config": {"url": "https://evil.test/mcp"}}
    for refs in (
        {"token": "store:connector/vendor/token"},
        {"token": "store:connector/other/token_x"},
        {"token": "store:project/billing-api/tracker/api_token"},
    ):
        refused = await client.post(ORG, json={**other, "secret_refs": refs})
        assert refused.status_code == 422 and "derived by the platform" in refused.text, refs
    # a field given twice is ambiguous, an empty value is not a value
    both = await client.post(
        ORG, json={**other, "secret_refs": {"token": "env:X"}, "secret_values": {"token": VALUE_1}}
    )
    assert both.status_code == 422 and "not both" in both.text
    empty = await client.post(ORG, json={**other, "secret_values": {"token": ""}})
    assert empty.status_code == 422
    assert (await client.get(f"{ORG}/other")).status_code == 404, "nothing half-created"
    # the connector's own derived reference, re-sent as a DTO round trip, is accepted
    again = await client.patch(
        f"{ORG}/vendor", json={"secret_refs": {"token": "store:connector/vendor/token"}}
    )
    assert again.status_code == 200, again.text


async def test_a_validation_error_never_echoes_the_value(client: AsyncClient, admin: str) -> None:
    refused = await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}, secret_refs=VALUE_2))
    assert refused.status_code == 422
    assert VALUE_1 not in refused.text and VALUE_2 not in refused.text
    unknown = await client.post(ORG, json=_declare(secret_values={"nope": VALUE_1}))
    assert unknown.status_code == 422 and VALUE_1 not in unknown.text


async def test_only_the_organisation_administrator_sets_an_organisation_secret(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    members = "/api/v1/orgs/varga/members"
    assert (
        await client.post(
            members, json={"email": "po@varga.dev", "role": "project_owner", "project_slug": "billing-api"}
        )
    ).status_code == 201
    client.cookies.clear()
    await login(client, "po@varga.dev")
    for call in (
        client.put(f"{ORG}/vendor/secrets/token", json={"value": VALUE_2}),
        client.delete(f"{ORG}/vendor/secrets/token"),
        client.patch(f"{ORG}/vendor", json={"secret_values": {"token": VALUE_2}}),
        client.post(ORG, json={**_declare(secret_values={"token": VALUE_2}), "name": "mine"}),
    ):
        assert (await call).status_code == 403
    # ...but a member may read the metadata: "set, rotated at" — never the value.
    seen = await client.get(f"{ORG}/vendor")
    assert seen.status_code == 200 and seen.json()["secrets"][0]["version"] == 1
    assert VALUE_1 not in seen.text and VALUE_2 not in seen.text
    assert [r.version for r in await _stored_rows()] == [1], "the refused writes stored nothing"


async def test_a_cleared_secret_leaves_the_connector_in_error_with_a_message(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    cleared = await client.delete(f"{ORG}/vendor/secrets/token")
    assert cleared.status_code == 200 and cleared.json()["secrets"] == []
    assert cleared.json()["secret_refs"] == {}
    assert await _stored_rows() == [], "the sealed value is deleted with its reference"
    assert (await client.delete(f"{ORG}/vendor/secrets/token")).status_code == 404
    # with no token the vendor refuses; and a stored reference whose value is gone says so
    refused = await client.post(f"{ORG}/vendor/discover")
    assert refused.status_code in {401, 502}, refused.text
    assert "connector.secret.clear" in await _audit_text()


async def test_a_reference_whose_stored_value_is_gone_fails_loudly(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    from choregos_api.db.models import OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    async with session_scope() as session:  # behind the API's back
        await session.execute(delete(OrgSecret))
    seen = (await client.get(f"{ORG}/vendor")).json()
    assert seen["secrets"][0]["source"] == "store" and seen["secrets"][0]["version"] is None
    refused = await client.post(f"{ORG}/vendor/discover")
    assert refused.status_code == 502 and "no value is stored" in refused.text
    assert (await client.get(f"{ORG}/vendor")).json()["status"] == "error"
    assert vendor.recues == [], "nothing was sent to the vendor without a key"


async def test_without_a_deployment_key_a_value_is_refused_and_a_reference_still_works(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_SECRETS_KEKS")
    monkeypatch.delenv("CHOREGOS_FAKES")
    refused = await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    assert refused.status_code == 409 and "CHOREGOS_SECRETS_KEKS" in refused.text
    assert VALUE_1 not in refused.text
    assert (await client.get(f"{ORG}/vendor")).status_code == 404, "nothing half-created"
    by_reference = await client.post(ORG, json=_declare(secret_refs={"token": "env:VENDOR_KEY"}))
    assert by_reference.status_code == 201, by_reference.text
    (state,) = by_reference.json()["secrets"]
    assert (state["source"], state["reference"]) == ("reference", "env:VENDOR_KEY")


async def test_an_edit_merges_and_a_reference_replaces_a_stored_value(
    client: AsyncClient, admin: str, vendor: Any
) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    edited = await client.patch(f"{ORG}/vendor", json={"config": {"url": "https://vendor2.test/mcp"}})
    assert edited.status_code == 200, edited.text
    assert edited.json()["config"]["url"] == "https://vendor2.test/mcp"
    assert edited.json()["secrets"][0]["version"] == 1, "the stored secret is kept"
    assert len(await _stored_rows()) == 1

    switched = await client.patch(f"{ORG}/vendor", json={"secret_refs": {"token": "env:VENDOR_KEY"}})
    assert switched.status_code == 200, switched.text
    assert switched.json()["secrets"][0]["source"] == "reference"
    assert await _stored_rows() == [], "a value no reference points to is deleted"


async def test_deleting_a_connector_deletes_its_sealed_secrets(client: AsyncClient, admin: str) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    assert len(await _stored_rows()) == 1
    assert (await client.delete(f"{ORG}/vendor")).status_code == 204
    assert await _stored_rows() == []


# ───────────────────────────── a project's connector ─────────────────────────────


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """A `tracker` type that records what its factory receives."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    received: list[dict[str, Any]] = []
    spec = ConnectorTypeSpec("probe", ("tracker",), {"type": "object", "properties": {}}, ("api_token",))
    register("tracker", "probe", spec)(lambda cfg: received.append(cfg) or object())
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield received
    finally:
        _REGISTRY.pop(("tracker", "probe"), None)
        _SPECS.pop(("tracker", "probe"), None)


async def test_a_project_connector_stores_its_secret_and_the_test_resolves_it(
    client: AsyncClient, project: dict[str, Any], probe: list[dict[str, Any]]
) -> None:
    pid = project["id"]
    put = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker",
        json={"type": "probe", "config": {}, "secret_values": {"api_token": VALUE_1}},
    )
    assert put.status_code == 200, put.text
    assert put.json()["secret_refs"] == {"api_token": "store:project/billing-api/tracker/api_token"}
    assert put.json()["secrets"][0]["source"] == "store" and VALUE_1 not in put.text
    assert VALUE_1 not in (await client.get(f"/api/v1/projects/{pid}/connectors")).text
    assert VALUE_1 not in (await client.get(f"/api/v1/projects/{pid}/requirements")).text

    result = await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")
    assert result.json()["ok"] is True, result.text
    assert probe == [{"api_token": VALUE_1}] and VALUE_1 not in result.text

    # saving the configuration again does not wipe the typed secret...
    again = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "probe", "config": {"x": 1}}
    )
    assert again.json()["secrets"][0]["version"] == 1
    # ...replacing it bumps the version, clearing it removes it
    replaced = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker/secrets/api_token", json={"value": VALUE_2}
    )
    assert replaced.json()["secrets"][0]["version"] == 2
    cleared = await client.delete(f"/api/v1/projects/{pid}/connectors/tracker/secrets/api_token")
    assert cleared.status_code == 200 and cleared.json()["secrets"] == []
    assert await _stored_rows() == []
    audit = await _audit_text()
    assert VALUE_1 not in audit and VALUE_2 not in audit


async def test_a_developer_cannot_set_a_project_secret(
    client: AsyncClient, project: dict[str, Any], probe: list[dict[str, Any]]
) -> None:
    pid = project["id"]
    await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "probe", "config": {}})
    await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    refused = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker/secrets/api_token", json={"value": VALUE_1}
    )
    assert refused.status_code == 403 and await _stored_rows() == []


async def test_a_project_cannot_borrow_the_organisation_s_secret(
    client: AsyncClient, project: dict[str, Any], probe: list[dict[str, Any]]
) -> None:
    """The exfiltration the derived names close: a project connector pointing at an org secret."""
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    refused = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "probe", "config": {}, "secret_refs": {"api_token": "store:connector/vendor/token"}},
    )
    assert refused.status_code == 422 and "derived by the platform" in refused.text


# ───────────────────────────── the rewrap, and the sync ─────────────────────────────


async def test_a_rewrap_after_a_key_rotation_keeps_every_secret_readable(
    client: AsyncClient, admin: str, vendor: Any, sealed_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.post(ORG, json=_declare(secret_values={"token": VALUE_1}))
    new_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k2:{new_key},k1:{sealed_key}")
    assert (await client.post(f"{ORG}/vendor/discover")).status_code == 200, "k1 still opens"
    done = await client.post("/api/v1/platform/secrets/rewrap")
    assert done.status_code == 200 and done.json() == {"rewrapped": 1}
    assert [r.kek_id for r in await _stored_rows()] == ["k2"]
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k2:{new_key}")  # k1 retired
    assert (await client.post(f"{ORG}/vendor/discover")).status_code == 200
    assert (await client.post("/api/v1/platform/secrets/rewrap")).json() == {"rewrapped": 0}


async def test_a_sharepoint_source_syncs_with_a_store_held_secret(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync used to resolve `env:` only: a `store:` reference raised and the source failed."""
    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.knowledge import sync as sync_module

    created = await client.post(
        "/api/v1/orgs/varga/knowledge",
        json={"slug": "framework", "name": "Framework", "serving_mode": "index"},
    )
    assert created.status_code == 201, created.text
    connector = await client.post(
        ORG,
        json={
            "name": "sharepoint",
            "kind": "documents",
            "type": "sharepoint",
            "config": {"tenant_id": "acme", "client_id": "choregos-kb"},
            "secret_values": {"client_secret": VALUE_1},
        },
    )
    assert connector.status_code == 201, connector.text
    source = (
        await client.post(
            "/api/v1/orgs/varga/knowledge/framework/sources",
            json={
                "kind": "sharepoint",
                "name": "Site",
                "connector": "sharepoint",
                "config": {"site_id": "acme.sharepoint.com,1,2"},
                "interval_minutes": 30,
            },
        )
    ).json()
    site = fake_source(source["id"])
    site.put("item-1", "note.md", b"# Note\n\nHello.\n")
    seen: list[dict[str, Any]] = []
    real_build = sync_module.build

    def spy(kind: str, type_name: str, config: dict[str, Any]) -> Any:
        seen.append(config)
        return real_build(kind, type_name, config)

    monkeypatch.setattr(sync_module, "build", spy)
    summary = await sync_module.sync_source("varga", source["id"])
    assert summary["added"] == 1, summary
    assert seen and seen[0]["client_secret"] == VALUE_1
