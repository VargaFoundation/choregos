"""The orchestrator builds a project's adapters with its SEALED connector secrets (S39-06c, ADR 0058).

The adapter factory is synchronous and knows nothing of the database: `load_project` reads the
`store:` values with the organisation's context and hands them over as `resolved`.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters import ConnectorTypeSpec, register
from choregos_adapters.registry import _REGISTRY, _SPECS

from .conftest import Fixture

KINDS = ("tracker", "scm", "ci", "cd", "runtime", "memory", "gateway", "notify")
VALUE = "sealed-tracker-token-77"


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, dict[str, Any]]]:
    received: dict[str, dict[str, Any]] = {}
    spec = ConnectorTypeSpec("probe", KINDS, {"type": "object", "properties": {}}, ("api_token",))
    for kind in KINDS:
        register(kind, "probe", spec)(lambda cfg, k=kind: received.__setitem__(k, cfg) or object())
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", "k1:" + base64.b64encode(os.urandom(32)).decode())
    try:
        yield received
    finally:
        for kind in KINDS:
            _REGISTRY.pop((kind, "probe"), None)
            _SPECS.pop((kind, "probe"), None)


async def _declare(setup: Fixture, refs: dict[str, str], value: str | None) -> None:
    from choregos_api.db.models import Connector, Project
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import SealedDbSecretStore

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for kind in KINDS:
            session.add(
                Connector(
                    project_id=project.id,
                    kind=kind,
                    type="probe",
                    config={},
                    secret_refs=refs if kind == "tracker" else None,
                )
            )
        if value is not None:
            await SealedDbSecretStore().put(
                session, project.org_id, "project/billing-api/tracker/api_token", value, "t@t.t"
            )


async def test_a_project_connector_with_a_sealed_secret_builds_its_adapter_with_the_value(
    setup: Fixture, probes: dict[str, dict[str, Any]]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    await _declare(setup, {"api_token": "store:project/billing-api/tracker/api_token"}, VALUE)
    async with session_scope(orgs="*") as session:
        await load_project(session, setup.project_id)
    assert probes["tracker"] == {"api_token": VALUE}
    assert "api_token" not in probes["scm"], "another connector gets nothing of it"


async def test_a_lost_sealed_value_fails_that_connector_only_and_loudly(
    setup: Fixture, probes: dict[str, dict[str, Any]]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_core.secrets import SecretIntrouvable
    from choregos_orchestrator.activities.base import load_project

    await _declare(setup, {"api_token": "store:project/billing-api/tracker/api_token"}, None)
    with pytest.raises(SecretIntrouvable, match="resolved by the platform"):
        async with session_scope(orgs="*") as session:
            await load_project(session, setup.project_id)
    assert "tracker" not in probes, "never an adapter built without its key"
