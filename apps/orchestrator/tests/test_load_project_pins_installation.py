"""`load_project` builds GitHub adapters with the installation the organisation claimed (ADR 0073)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters import ConnectorTypeSpec, register
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.registry import _REGISTRY, _SPECS

from .conftest import Fixture

KINDS = ("ci", "cd", "runtime", "memory", "gateway", "notify")
GITHUB = {"tracker": "github-issues", "scm": "github"}


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, dict[str, Any]]]:
    received: dict[str, dict[str, Any]] = {}
    spec = ConnectorTypeSpec("probe", KINDS, {"type": "object", "properties": {}})
    for kind in KINDS:
        register(kind, "probe", spec)(lambda cfg, k=kind: received.__setitem__(k, cfg) or object())
    for kind, type_name in GITHUB.items():
        monkeypatch.setitem(
            _REGISTRY, (kind, type_name), lambda cfg, k=kind: received.__setitem__(k, cfg) or object()
        )
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield received
    finally:
        for kind in KINDS:
            _REGISTRY.pop((kind, "probe"), None)
            _SPECS.pop((kind, "probe"), None)


async def _declare(setup: Fixture, *, second_org: bool, claimed: bool) -> None:
    from choregos_api.db.models import Connector, GitHubInstallation, Organization, Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for kind in KINDS:
            session.add(Connector(project_id=project.id, kind=kind, type="probe", config={}))
        for kind, type_name in GITHUB.items():
            session.add(
                Connector(
                    project_id=project.id,
                    kind=kind,
                    type=type_name,
                    config={"repo": "varga-inc/billing", "installation_id": 999},
                )
            )
        if second_org:
            session.add(Organization(slug="globex", name="Globex"))
        if claimed:
            session.add(
                GitHubInstallation(
                    id=55,
                    account_login="varga-inc",
                    account_key="varga-inc",
                    account_id=550,
                    account_type="Organization",
                    org_id=project.org_id,
                )
            )


async def test_load_project_passes_installation_id_for_github_kinds(
    setup: Fixture, probes: dict[str, dict[str, Any]]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    await _declare(setup, second_org=False, claimed=True)
    async with session_scope(orgs="*") as session:
        await load_project(session, setup.project_id)
    assert probes["scm"]["installation_id"] == 55, "the claimed one, not the legacy value stored"
    assert probes["tracker"]["installation_id"] == 55
    assert "installation_id" not in probes["ci"], "other kinds are left alone"


async def test_a_single_org_instance_without_a_claim_still_discovers(
    setup: Fixture, probes: dict[str, dict[str, Any]]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    await _declare(setup, second_org=False, claimed=False)
    async with session_scope(orgs="*") as session:
        await load_project(session, setup.project_id)
    assert "installation_id" not in probes["scm"], "no legacy value either: discovery, today's behaviour"


async def test_a_multi_org_instance_without_a_claim_refuses_to_build(
    setup: Fixture, probes: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import load_project

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "1")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "pem")
    reset_settings_cache()
    try:
        await _declare(setup, second_org=True, claimed=False)
        async with session_scope(orgs="*") as session:
            with pytest.raises(ConfigurationError, match="not installed on `varga-inc`"):
                await load_project(session, setup.project_id)
    finally:
        reset_settings_cache()
