"""A fresh installation must be able to serve itself.

State of play of 2026-09-24: no way to create an organisation, none to file a request on an
internal tracker, none to issue the first token. The product only started with a seed script that
writes to the database.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login


async def test_the_bootstrap_creates_the_organisation_and_its_admins_only_once(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.bootstrap import bootstrap_installation
    from choregos_api.config import Settings

    settings = Settings(
        bootstrap_org="acme", bootstrap_org_name="ACME", bootstrap_admins="Alice@acme.test, bob@acme.test"
    )
    first = await bootstrap_installation(settings)
    assert first == {  # lang: fr-ok (the returned keys are unchanged: ADR 0098)
        "organisations": ["acme"],
        "administrateurs": ["alice@acme.test", "bob@acme.test"],
    }
    second = await bootstrap_installation(settings)
    assert second == {"organisations": [], "administrateurs": []}, (
        "replaying does not touch what exists"
    )  # lang: fr-ok
    assert await bootstrap_installation(Settings(bootstrap_org="")) == {  # lang: fr-ok
        "organisations": [],
        "administrateurs": [],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice@acme.test")
        orgs = (await client.get("/api/v1/orgs")).json()
        assert [(o["slug"], o["role"]) for o in orgs] == [("acme", "org_admin")]


async def test_an_admin_creates_an_organisation_a_developer_does_not(client: AsyncClient, admin: str) -> None:
    """The check on RIGHTS comes before the one on the EDITION, and that is deliberate.

    A developer receives 403 without learning anything about the edition or the number of
    organisations; an administrator receives 409 and the reason. A product limit is not told to
    someone who has no right to meet it.

    This test used to create a second organisation; the community edition only holds one
    (ADR 0024), so it now creates it in the enterprise edition, which proves in passing that the
    guard is about the edition and not a hard-coded cap.
    """
    from choregos_api import edition

    refusal = await client.post("/api/v1/orgs", json={"slug": "subsidiary", "name": "The subsidiary"})
    assert refusal.status_code == 409, refusal.text
    assert "community" in refusal.json()["detail"]

    edition.declare(edition.ENTERPRISE)
    try:
        created = await client.post("/api/v1/orgs", json={"slug": "subsidiary", "name": "The subsidiary"})
        assert created.status_code == 201, created.text
        assert created.json()["role"] == "org_admin"
        again = await client.post("/api/v1/orgs", json={"slug": "subsidiary", "name": "again"})
        assert again.status_code == 409, "a slug already taken stays a conflict"
        slugs = [o["slug"] for o in (await client.get("/api/v1/orgs")).json()]
        assert slugs == ["subsidiary", "varga"]

        await login(client, "dev@varga.dev")
        assert (await client.post("/api/v1/orgs", json={"slug": "pirate", "name": "x"})).status_code == 403
    finally:
        edition.reset()


async def test_a_request_is_filed_in_choregos_when_the_tracker_is_internal(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert put.status_code in {200, 201}, put.text
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "Data project manager", "body": "6 months, Lille", "size": "M"},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["tracker_key"] == "BILLING-API-1"
    assert item["state"] == "inbox"
    assert item["temporal_wf_id"] == "wi-billing-api-BILLING-API-1"
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal) and item["temporal_wf_id"] in fake.started

    second = (
        await client.post(
            f"/api/v1/projects/{project['id']}/work-items", json={"title": "Two devs", "start": False}
        )
    ).json()
    assert second["tracker_key"] == "BILLING-API-2" and second["temporal_wf_id"] is None

    # An external tracker no longer refuses: see test_work_item_external_tracker.py.
