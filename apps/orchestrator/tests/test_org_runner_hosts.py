"""The hosts an organisation opens for its runners join every run's allowlist and the egress proxy
(S39-22d, ADR 0066). Activities only: nothing under `workflows/` reads the table."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.gitops import EGRESS_NAME, render_project_manifests
from choregos_orchestrator.org_egress import merged_domains, org_runner_hosts, runner_domain

from .conftest import Fixture


async def _rule(setup: Fixture, host: str, used_by: list[str], status: str = "allowed") -> None:
    from choregos_api.db.models import OrgEgressRule, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        session.add(OrgEgressRule(org_id=project.org_id, host=host, used_by=used_by, status=status))


def _plan(setup: Fixture) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "dev",
        "attempt": 1,
        "backend": "claude-code",
        "model_request": "profile:by_size",
    }


async def _domains(setup: Fixture) -> list[str]:
    prepared = await stage_activities.prepare_stage(_plan(setup))
    return list(prepared["stage_input"]["permissions"]["allow_domains"])


def test_a_wildcard_is_the_domain_the_guard_understands_and_an_ip_is_none() -> None:
    assert runner_domain("*.example.test") == "example.test"
    assert runner_domain("a2a.example.test") == "a2a.example.test"
    assert runner_domain("10.0.0.7") is None
    assert merged_domains(["b.test", "a.test"], ["a.test", "c.test"]) == ["a.test", "b.test", "c.test"]


@pytest.mark.asyncio
async def test_a_runners_rule_joins_the_stage_input_and_an_api_rule_does_not(setup: Fixture) -> None:
    before = await _domains(setup)
    assert "a2a.example.test" not in before
    await _rule(setup, "a2a.example.test", ["runners"])
    await _rule(setup, "mcp.example.test", ["api"])
    await _rule(setup, "*.wide.example.test", ["api", "runners"])
    await _rule(setup, "revoked.example.test", ["runners"], status="revoked")
    await _rule(setup, "waiting.example.test", ["runners"], status="requested")
    # a new run id: the first stage input is reused by `prepare_stage` (replay)
    plan = {**_plan(setup), "attempt": 2}
    after = (await stage_activities.prepare_stage(plan))["stage_input"]["permissions"]["allow_domains"]
    assert set(after) == set(before) | {"a2a.example.test", "wide.example.test"}
    assert "mcp.example.test" not in after and "revoked.example.test" not in after
    assert "waiting.example.test" not in after


@pytest.mark.asyncio
async def test_the_union_reads_only_the_organisations_own_rules(setup: Fixture) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    await _rule(setup, "a2a.example.test", ["runners"])
    async with session_scope() as session:
        other = Organization(slug="other", name="Other")
        session.add(other)
        await session.flush()
        assert await org_runner_hosts(session, other.id) == []


def test_the_proxy_and_the_network_policy_render_the_union() -> None:
    config = ProjectConfig(slug="billing", org="varga", repo=RepoConfig(url="https://github.com/varga/b.git"))
    policy = load_preset("team")
    plain = render_project_manifests("billing", config, policy)
    joined = render_project_manifests("billing", config, policy, org_allow_domains=["a2a.example.test"])

    def conf(files: dict[str, str]) -> str:
        docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(files["egress.yaml"])}
        return str(docs[("ConfigMap", EGRESS_NAME)]["data"]["squid.conf"])

    assert ".a2a.example.test" not in conf(plain)
    assert ".a2a.example.test" in conf(joined)
    assert (
        "a2a.example.test" in joined["network-policies.yaml"] if "network-policies.yaml" in joined else True
    )
    for domain in policy.sandbox.network.allow_domains:
        assert f".{domain}" in conf(joined), "the project's own domains stay"


@pytest.mark.asyncio
async def test_a_remote_agents_admission_reads_the_union_not_the_project_alone(setup: Fixture) -> None:
    """S39-08b: the host the card pins must be allowed; an administrator's `runners` rule is enough."""
    from types import SimpleNamespace

    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.remote import check_remote_agent
    from temporalio.exceptions import ApplicationError

    runtime = SimpleNamespace(
        pinned_card=SimpleNamespace(url="https://a2a.example.test/v1/rpc"), credential=None
    )
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        bundle = SimpleNamespace(
            project=project, engine=SimpleNamespace(allow_domains=lambda: ["github.com"])
        )
        with pytest.raises(ApplicationError, match="allow_domains"):
            await check_remote_agent(session, bundle, runtime)
    await _rule(setup, "a2a.example.test", ["runners"])
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        bundle = SimpleNamespace(
            project=project, engine=SimpleNamespace(allow_domains=lambda: ["github.com"])
        )
        await check_remote_agent(session, bundle, runtime)


@pytest.mark.asyncio
async def test_a_runners_rule_the_ceiling_denies_does_not_join_the_run(setup: Fixture) -> None:
    """ADR 0070, amended at integration: what the organisation opened for its runners joins a run as
    an implied host, and the deny lists still win over it."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope

    await _rule(setup, "a2a.example.test", ["runners"])
    await _rule(setup, "blocked.example.test", ["runners"])
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        org = await session.get(Organization, project.org_id)
        assert org is not None
        ceiling = {"network": {"deny_domains": ["blocked.example.test"]}}
        org.settings = {**(org.settings or {}), "policy_ceiling": {"ceiling": ceiling}}
    permissions = (await stage_activities.prepare_stage(_plan(setup)))["stage_input"]["permissions"]
    assert "a2a.example.test" in permissions["allow_domains"]
    assert "blocked.example.test" not in permissions["allow_domains"]
    assert "blocked.example.test" in permissions["deny_domains"]


@pytest.mark.asyncio
async def test_a_remote_agents_host_a_deny_list_names_is_refused_even_when_opened(setup: Fixture) -> None:
    from types import SimpleNamespace

    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.remote import check_remote_agent
    from temporalio.exceptions import ApplicationError

    await _rule(setup, "a2a.example.test", ["runners"])
    runtime = SimpleNamespace(
        pinned_card=SimpleNamespace(url="https://a2a.example.test/v1/rpc"), credential=None
    )
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        bundle = SimpleNamespace(project=project, engine=SimpleNamespace(allow_domains=lambda: []))
        await check_remote_agent(session, bundle, runtime)
        with pytest.raises(ApplicationError, match="denied by the network policy"):
            await check_remote_agent(session, bundle, runtime, deny=["example.test"])
