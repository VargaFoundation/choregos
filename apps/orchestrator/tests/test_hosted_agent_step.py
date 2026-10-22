"""A hosted agent (ADR 0080) carries a step: its pinned image beside the runner, nothing secret in it."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities
from temporalio.exceptions import ApplicationError

from .conftest import Fixture
from .test_code_agent_step import _agent, _plan

pytestmark = pytest.mark.asyncio

DIGEST = "sha256:" + "c" * 64
PINNED = f"ghcr.io/acme/triager:1@{DIGEST}"


def _hosted(**extra: Any) -> dict[str, Any]:
    source = {"type": "image", "ref": "ghcr.io/acme/triager:1", "digest": DIGEST}
    return {"kind": "hosted", "source": source, **extra}


async def _start(setup: Fixture, prepared: dict[str, Any]) -> Any:
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    return setup.adapters.executor.started[-1]


async def test_a_hosted_agent_is_sent_the_step_in_its_pinned_image(setup: Fixture) -> None:
    protocols = ["invocations", "responses", "invocations_ws"]
    await _agent(setup, _hosted(protocols=protocols, env={"LOG_LEVEL": "debug"}))
    prepared = await stage_activities.prepare_stage(_plan(setup))
    stage_input = prepared["stage_input"]
    assert stage_input["agent"]["backend"] == "hosted" and stage_input["agent"]["image"] is None
    hosted = stage_input["hosted"]
    assert hosted["image"] == PINNED, "the digest publication pinned, never the tag alone"
    assert hosted["protocol"] == "invocations", "the first HTTP protocol the version lists"
    assert hosted["agent_slug"] == "triager" and hosted["agent_version"] == 1
    assert hosted["env"] == {"LOG_LEVEL": "debug"}
    assert hosted["socket"] is False, "a step has no caller's socket to relay (S41-21)"
    assert stage_input["repo"] is None, "the container never sees a workspace (`workspace: none`)"
    assert stage_input["skills"] == []
    assert stage_input["budget"]["max_turns"] == 9


async def test_the_pod_runs_the_runner_and_the_agent_beside_it_without_any_secret(setup: Fixture) -> None:
    from choregos_orchestrator.config import get_settings

    await _agent(setup, _hosted(termination_grace_s=40))
    spec = await _start(setup, await stage_activities.prepare_stage(_plan(setup)))
    assert spec.runner_image == get_settings().runner_image, "the runner is the platform's own"
    agent = spec.agent_container
    assert agent is not None and agent.image == PINNED and agent.termination_grace_s == 40
    assert agent.env["OPENAI_BASE_URL"] == "http://127.0.0.1:4001/v1"
    assert agent.env["OPENAI_API_KEY"] == "choregos-loopback"
    assert agent.env["CHOREGOS_RUN_ID"] == spec.run_id
    secrets = [spec.run_token, spec.gateway_key, spec.remote_token]
    assert all(value not in agent.env.values() for value in secrets if value)
    assert not {name for name in agent.env if "TOKEN" in name or "GATEWAY_KEY" in name}


@pytest.mark.parametrize("workspace", ["read", "write"])
async def test_a_hosted_agent_that_asks_for_the_workspace_gets_the_repository(
    setup: Fixture, workspace: str
) -> None:
    """S41-18: the runner clones the repository and gives the container a copy of it; under
    `write` it commits and pushes what came back, so the step names its branch."""
    await _agent(setup, _hosted(workspace=workspace))
    spec = await _start(setup, prepared := await stage_activities.prepare_stage(_plan(setup)))
    stage_input = prepared["stage_input"]
    assert stage_input["hosted"]["workspace"] == workspace
    assert stage_input["repo"] is not None and stage_input["repo"]["work_branch"]
    assert spec.agent_container is not None and spec.agent_container.workspace == workspace
    assert spec.agent_container.env["CHOREGOS_WORKSPACE"] == "/workspace"


async def test_an_actor_backend_on_a_hosted_agent_is_refused(setup: Fixture) -> None:
    await _agent(setup, _hosted())
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup, backend="codex"))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable
    assert "hosted agent" in str(raised.value)


@pytest.mark.parametrize(
    ("runtime", "refusal"),
    [
        (
            {"kind": "hosted", "source": {"type": "image", "ref": "ghcr.io/acme/triager:1"}},
            "hosted_image_unpinned",
        ),
        (
            {
                "kind": "hosted",
                "source": {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]},
                "build": {"status": "building", "key": DIGEST},
            },
            "hosted_build_not_ready",
        ),
        (
            {
                "kind": "hosted",
                "source": {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]},
            },
            "hosted_build_not_ready",
        ),
        (
            {
                "kind": "hosted",
                "source": {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]},
                "build": {"status": "failed", "key": DIGEST, "image": f"registry.internal/t@{DIGEST}"},
            },
            "hosted_build_not_ready",
        ),
        (
            {
                "kind": "hosted",
                "source": {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]},
                "build": {"status": "ready", "key": DIGEST, "image": "registry.internal/t:latest"},
            },
            "hosted_build_not_ready",
        ),
        (_hosted(protocols=["invocations_ws"]), "hosted_protocol_unsupported"),
    ],
)
async def test_a_hosted_step_no_pod_could_run_is_refused_before_any_key(
    setup: Fixture, runtime: dict[str, Any], refusal: str
) -> None:
    await _agent(setup, runtime)
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup))
    assert raised.value.type == refusal and raised.value.non_retryable
    assert "`triager` v1" in str(raised.value)
    assert setup.adapters.gateway.keys == {}


async def test_a_zip_whose_build_is_ready_runs_the_built_image(setup: Fixture) -> None:
    built = f"registry.internal/choregos-builds/triager@{DIGEST}"
    zipped = {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]}
    await _agent(
        setup,
        {"kind": "hosted", "source": zipped, "build": {"status": "ready", "key": DIGEST, "image": built}},
    )
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["stage_input"]["hosted"]["image"] == built


async def test_a_zip_version_published_pending_runs_the_image_its_build_row_records(setup: Fixture) -> None:
    """Versions are immutable: a zip version keeps `build.status: pending` as published, and the step
    reads the `hosted_builds` row, which the build workflow wrote since (S41-08)."""
    from choregos_api.db.models import HostedBuildRecord, Project
    from choregos_api.db.session import session_scope

    built = f"registry.internal/choregos-builds/triager@{DIGEST}"
    zipped = {"type": "zip", "sha256": DIGEST, "runtime": "python_3_12", "entry_point": ["app"]}
    await _agent(setup, {"kind": "hosted", "source": zipped, "build": {"status": "pending", "key": DIGEST}})
    with pytest.raises(ApplicationError, match="build is pending"):
        await stage_activities.prepare_stage(_plan(setup))
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        row = HostedBuildRecord(org_id=project.org_id, key=DIGEST, status="building", attempt=1)
        session.add(row)
        await session.flush()
        row_id = row.id
    with pytest.raises(ApplicationError, match="build is building"):
        await stage_activities.prepare_stage(_plan(setup))
    async with session_scope() as session:
        found = await session.get(HostedBuildRecord, row_id)
        assert found is not None
        found.status, found.image = "ready", built
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["stage_input"]["hosted"]["image"] == built


async def test_a_cluster_older_than_1_29_fails_the_hosted_run_without_a_retry(setup: Fixture) -> None:
    """S41-16: below 1.29 the sidecar would be a plain init container the pod never gets past."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    setup.adapters.executor.server_version = "v1.28.9-gke.1200"
    await _agent(setup, _hosted())
    prepared = await stage_activities.prepare_stage(_plan(setup))
    with pytest.raises(ApplicationError) as raised:
        await _start(setup, prepared)
    assert raised.value.type == "hosted_unsupported_cluster" and raised.value.non_retryable
    assert "Kubernetes 1.29" in str(raised.value) and "runs 1.28" in str(raised.value)
    assert setup.adapters.executor.started == []
    async with session_scope() as session:
        run = await session.get(Run, prepared["stage_input"]["run_id"])
        assert run is not None and run.status == "failed"
        assert run.result is not None and run.result["reason"] == "hosted_unsupported_cluster"


@pytest.mark.parametrize("version", ["v1.29.3-eks-abc", "1.30+", "v1.33.1"])
async def test_a_cluster_from_1_29_runs_the_hosted_step(setup: Fixture, version: str) -> None:
    setup.adapters.executor.server_version = version
    await _agent(setup, _hosted())
    spec = await _start(setup, await stage_activities.prepare_stage(_plan(setup)))
    assert spec.agent_container is not None
