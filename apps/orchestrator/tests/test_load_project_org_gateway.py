"""A project without a gateway of its own runs on its organisation's (S44-04).

The organisation's model catalogue already listed the models of the gateway it brings (ADR 0060); a
run minted its key on the deployment's gateway and its agent called the deployment's URL. The run's
key and the agent's URL now come from the organisation's gateway; a project's own gateway still wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters import ConnectorTypeSpec, register
from choregos_adapters.registry import _REGISTRY, _SPECS

from .conftest import Fixture

ORG_URL = "https://llm.acme.example/v1"
#: Every other kind a probe too, declared by the project: without fakes, a default would reach a cluster.
OTHERS = ("tracker", "scm", "ci", "cd", "runtime", "memory", "notify")


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    received: list[dict[str, Any]] = []
    spec = ConnectorTypeSpec("probe", ("gateway", *OTHERS), {"type": "object", "properties": {}})
    register("gateway", "probe", spec)(lambda cfg: received.append(cfg) or object())
    for kind in OTHERS:
        register(kind, "probe", spec)(lambda cfg: object())
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield received
    finally:
        for kind in ("gateway", *OTHERS):
            _REGISTRY.pop((kind, "probe"), None)
            _SPECS.pop((kind, "probe"), None)


async def _org_gateway(setup: Fixture) -> None:
    from choregos_api.db.models import Connector, OrgConnector, Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for kind in OTHERS:
            session.add(Connector(project_id=project.id, kind=kind, type="probe", config={}))
        session.add(
            OrgConnector(
                org_id=project.org_id,
                name="acme-llm",
                kind="gateway",
                type="probe",
                config={"base_url": ORG_URL},
            )
        )


async def test_a_project_without_a_gateway_runs_on_its_organisations(
    setup: Fixture, built: list[dict[str, Any]]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    await _org_gateway(setup)
    async with session_scope(orgs="*") as session:
        bundle = await load_project(session, setup.project_id)
    assert built == [{"base_url": ORG_URL}], "the gateway is built from the organisation's connector"
    assert bundle.gateway_url == ORG_URL, "the agent calls the gateway its key was minted on"


async def test_the_projects_own_gateway_still_wins(setup: Fixture, built: list[dict[str, Any]]) -> None:
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    await _org_gateway(setup)
    async with session_scope(orgs="*") as session:
        session.add(
            Connector(
                project_id=setup.project_id,
                kind="gateway",
                type="probe",
                config={"base_url": "http://own:4000"},
            )
        )
    async with session_scope(orgs="*") as session:
        bundle = await load_project(session, setup.project_id)
    assert built == [{"base_url": "http://own:4000"}]
    assert bundle.gateway_url == "http://own:4000", "the agent calls the gateway its key comes from"


async def test_without_any_gateway_connector_nothing_changes(setup: Fixture) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    async with session_scope(orgs="*") as session:
        bundle = await load_project(session, setup.project_id)
    assert bundle.gateway_url is None
