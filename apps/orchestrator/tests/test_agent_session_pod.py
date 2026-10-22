"""A hosted session's pod (S41-11): the runner in `serve-session` mode beside the agent's pinned
image, the session's key and token in its Secret only, started once; stopping it fails the turns
still waiting and closes the session."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from choregos_orchestrator.activities import agent_session as pod_activities
from temporalio.exceptions import ApplicationError

from .conftest import Fixture
from .test_code_agent_step import _agent

pytestmark = pytest.mark.asyncio

DIGEST = "sha256:" + "c" * 64
PINNED = f"ghcr.io/acme/triager:1@{DIGEST}"
KEY = "sk-session-secret"


def _hosted(**extra: Any) -> dict[str, Any]:
    source = {"type": "image", "ref": "ghcr.io/acme/triager:1", "digest": DIGEST}
    return {"kind": "hosted", "source": source, **extra}


async def _session(setup: Fixture) -> str:
    from choregos_api.db.models import AgentSessionRecord, Project
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services.agent_sessions import secret_name
    from choregos_core import utcnow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        record = AgentSessionRecord(
            org_id=project.org_id,
            project_id=project.id,
            agent_slug="triager",
            agent_version=1,
            user_id="alice@example.org",
            model="openai/gpt-4.1-mini",
            expires_at=utcnow() + timedelta(hours=1),
            pod={"state": "requested"},
        )
        session.add(record)
        await session.flush()
        await get_secret_store().put(
            session, project.org_id, secret_name(record.id), KEY, "alice@example.org"
        )
        return record.id


async def _events(session_id: str) -> list[str]:
    from choregos_api.db.models import AgentSessionEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(AgentSessionEvent.type)
            .where(AgentSessionEvent.session_id == session_id)
            .order_by(AgentSessionEvent.seq)
        )
        return list(rows.scalars())


async def _record(session_id: str) -> Any:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(AgentSessionRecord, session_id)


async def test_the_pod_runs_the_runner_serving_the_session_beside_the_pinned_image(setup: Fixture) -> None:
    await _agent(setup, _hosted(protocols=["responses"]))
    session_id = await _session(setup)
    pod = await pod_activities.start_session_pod({"session_id": session_id})
    (spec,) = setup.adapters.executor.started
    assert spec.run_id == f"session-{session_id}"
    assert spec.runner_command == ["serve-session"]
    assert spec.gateway_key == KEY, "the session's key reaches the pod through its Secret"
    agent = spec.agent_container
    assert agent is not None and agent.image == PINNED
    assert agent.env["CHOREGOS_MCP_SERVERS"] == "choregos", "the session's tools, served by the runner"
    assert agent.env["CHOREGOS_RUN_ID"] == spec.run_id
    assert all(value not in agent.env.values() for value in (spec.run_token, KEY))
    assert not {name for name in agent.env if "TOKEN" in name or "GATEWAY_KEY" in name}
    record = await _record(session_id)
    assert record.pod == pod and pod["state"] == "running" and pod["name"] == f"fake-session-{session_id}"
    stored = record.stage_input
    assert stored["hosted"]["image"] == PINNED and stored["repo"] is None
    assert stored["budget"]["max_turns"] == 9, "the agent's `limits.max_turns`, per response"
    assert stored["gateway_key"] is None and stored["callbacks"]["run_token"] != spec.run_token
    assert KEY not in str(stored) and spec.run_token not in str(stored)


async def test_a_session_pod_gets_no_workspace_whatever_the_version_asks(setup: Fixture) -> None:
    """S41-18: a session works on no repository, so a version published with `workspace: write`
    still gets no shared tree in its session pod."""
    await _agent(setup, _hosted(workspace="write"))
    session_id = await _session(setup)
    await pod_activities.start_session_pod({"session_id": session_id})
    (spec,) = setup.adapters.executor.started
    assert spec.agent_container is not None and spec.agent_container.workspace == "none"
    assert "CHOREGOS_WORKSPACE" not in spec.agent_container.env
    assert (await _record(session_id)).stage_input["hosted"]["workspace"] == "none"


async def test_a_second_start_starts_nothing(setup: Fixture) -> None:
    await _agent(setup, _hosted())
    session_id = await _session(setup)
    first = await pod_activities.start_session_pod({"session_id": session_id})
    again = await pod_activities.start_session_pod({"session_id": session_id})
    assert again == first
    assert len(setup.adapters.executor.started) == 1
    assert setup.adapters.executor.start_calls_by_run == {f"session-{session_id}": 1}


@pytest.mark.parametrize(
    ("protocols", "socket", "protocol"),
    [(["invocations_ws"], True, "responses"), (["invocations", "invocations_ws"], True, "invocations"),
     (["responses"], False, "responses")],
)  # fmt: skip
async def test_a_version_that_serves_a_socket_gets_its_socket_relayed(
    setup: Fixture, protocols: list[str], socket: bool, protocol: str
) -> None:
    """S41-21: a session's pod relays the caller's socket to `/invocations_ws`, which a workflow
    step refuses (`test_hosted_agent_step`)."""
    await _agent(setup, _hosted(protocols=protocols))
    session_id = await _session(setup)
    await pod_activities.start_session_pod({"session_id": session_id})
    hosted = (await _record(session_id)).stage_input["hosted"]
    assert hosted["socket"] is socket and hosted["protocol"] == protocol


async def test_a_version_no_pod_could_run_is_refused_without_a_retry(setup: Fixture) -> None:
    await _agent(setup, {"kind": "hosted", "source": {"type": "image", "ref": "ghcr.io/acme/triager:1"}})
    session_id = await _session(setup)
    with pytest.raises(ApplicationError) as raised:
        await pod_activities.start_session_pod({"session_id": session_id})
    assert raised.value.type == "hosted_image_unpinned" and raised.value.non_retryable
    assert setup.adapters.executor.started == []


async def test_stopping_fails_the_waiting_turns_cancels_the_pod_and_closes_the_session(
    setup: Fixture,
) -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.agent_hosted_sessions import next_turn
    from choregos_api.services.agent_sessions import journal

    await _agent(setup, _hosted())
    session_id = await _session(setup)
    await pod_activities.start_session_pod({"session_id": session_id})
    async with session_scope() as session:
        record = await session.get(AgentSessionRecord, session_id)
        await journal(session, record, "turn.queued", {"turn_id": "resp_1", "input": "hello"})

    stopped = await pod_activities.stop_session_pod({"session_id": session_id, "reason": "idle"})
    assert stopped == {"state": "stopped", "failed_turns": 1}
    assert setup.adapters.executor.cancelled == [f"session-{session_id}"]
    record = await _record(session_id)
    assert record.pod["state"] == "stopped"
    assert record.status == "closed" and record.reason == "idle"
    async with session_scope() as session:
        assert await next_turn(session, record) is None, "no response waits on a stopped pod"

    again = await pod_activities.stop_session_pod({"session_id": session_id, "reason": "idle"})
    assert again == {"state": "stopped", "failed_turns": 0}, "replayed, it changes nothing"
    assert setup.adapters.executor.cancelled == [f"session-{session_id}"]
    assert (await _events(session_id)).count("pod.stopped") == 1, "journaled once, replayed or not"


async def test_a_cluster_older_than_1_29_refuses_the_pod_without_a_retry(setup: Fixture) -> None:
    """S41-16: the session's agent is a native sidecar too."""
    setup.adapters.executor.server_version = "1.28"
    await _agent(setup, _hosted())
    session_id = await _session(setup)
    with pytest.raises(ApplicationError) as raised:
        await pod_activities.start_session_pod({"session_id": session_id})
    assert raised.value.type == "hosted_unsupported_cluster" and raised.value.non_retryable
    assert setup.adapters.executor.started == []

    setup.adapters.executor.server_version = "v1.29.0"
    pod = await pod_activities.start_session_pod({"session_id": session_id})
    assert pod["state"] == "running", "the same session starts once the cluster is upgraded"
