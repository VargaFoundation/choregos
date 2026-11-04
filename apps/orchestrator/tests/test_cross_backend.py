"""Cross review is a mechanism, not an intention: the backend really changes."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities

from .conftest import Fixture
from .test_backend_toggle_enforced import _switch

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _every_backend_enabled(setup: Fixture) -> None:
    """Review rotation is the subject here, not the platform's switch (S42-07c): on a fresh install
    only `claude-code` is enabled, and a reviewer is never drawn from a disabled backend."""
    from choregos_core import KNOWN_BACKEND_NAMES

    for name in KNOWN_BACKEND_NAMES:
        await _switch(name, enabled=True)


async def _set_cross_backend(project_id: str, value: bool) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        model.review.cross_backend = value
        row.json_doc = model.model_dump(mode="json", exclude_none=True)


async def _implemented_by(setup: Fixture, backend: str) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            Run(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                attempt=1,
                backend=backend,
                status="succeeded",
            )
        )


def _plan(setup: Fixture, role: str, backend: str | None = None) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": f"t-{role}",
        "role": role,
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "checker",
        "attempt": 1,
        "backend": backend,
        "model_request": "profile:by_size",
    }


async def test_the_reviewer_is_not_the_implementer(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    backend = prepared["stage_input"]["agent"]["backend"]
    assert backend != "codex", "the policy requires another reviewer"
    assert backend in {"claude-code", "codex", "gemini-cli", "goose", "opencode", "copilot-cli"}


async def test_without_the_policy_the_requested_backend_is_respected(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, False)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"


async def test_a_reviewer_already_different_is_not_changed(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="goose"))

    assert prepared["stage_input"]["agent"]["backend"] == "goose"


async def test_the_other_roles_are_not_touched(setup: Fixture) -> None:
    """Only the review is crossed: the implementation keeps the project's backend."""
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "implement", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"


async def test_a_single_allowed_backend_degrades_without_blocking(setup: Fixture) -> None:
    """A project that allows only one backend must not see its work items block."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        config = dict(project.config)
        config["agent"] = {"default_backend": "codex", "allowed_backends": ["codex"]}
        project.config = config

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"
    assert prepared["run_id"], "the work item moves on despite the degraded review"


# ──────── only the MCP servers that exist are announced to the agent (2026-09-27) ────────


async def test_the_tools_server_is_announced_to_every_agent() -> None:
    """The address is local, and someone ALWAYS serves it — sidecar under Tekton, runner elsewhere.

    I briefly made this announcement conditional, believing no server existed under `k8s_job`.
    The condition removed a true announcement and deprived the agent of its tools:
    `runner/local_tools.py` starts the server when nothing answers, but the agent only knows it
    if it is told.
    """
    from choregos_orchestrator.activities.stage import _mcp_servers
    from choregos_orchestrator.config import OrchestratorSettings

    for executor in ("tekton", "k8s_job", "local_docker", "aca"):
        servers = _mcp_servers(
            OrchestratorSettings(executor_kind=executor, memory_url="http://ecphoria:8432")
        )
        assert servers["choregos"].url == "http://localhost:7777/mcp", executor


async def test_the_memory_url_comes_from_its_setting_not_from_a_port_replacement() -> None:
    """It was built by `gateway_url.replace("4000", "8432")`.

    The replacement only holds if the gateway and the memory share a host. On the dev tenant, the
    gateway is `http://litellm:4000`: the memory became `http://litellm:8432` — measured with no
    answer in eight seconds, whereas `http://ecphoria:8432/mcp` answers 401 in nine milliseconds.
    """
    from choregos_orchestrator.activities.stage import _mcp_servers
    from choregos_orchestrator.config import OrchestratorSettings

    settings_under_test = OrchestratorSettings(
        executor_kind="k8s_job", gateway_url="http://litellm:4000", memory_url="http://ecphoria:8432"
    )
    servers = _mcp_servers(settings_under_test)
    assert servers["memory"].url == "http://ecphoria:8432/mcp", servers["memory"].url
    assert "litellm" not in servers["memory"].url, "the memory does not live on the gateway host"

    # Without a configured memory, no memory server is announced.
    assert "memory" not in _mcp_servers(OrchestratorSettings(executor_kind="k8s_job", memory_url=""))
