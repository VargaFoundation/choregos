"""An agent declares the workspace it needs (S51-03, ADR 0119): the spec, the egress proxy, the scope."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from .test_network_policy_api import AGENTS, LEGACY_DIGEST, LEGACY_SPEC, _policy, _run

DIGEST = "sha256:" + "c" * 64
HOSTED = {"kind": "hosted", "source": {"type": "image", "ref": "ghcr.io/acme/triager:1", "digest": DIGEST}}
A2A = {"kind": "a2a", "card_url": "https://rovo.example/.well-known/agent.json"}
REPO = {"url": "https://github.com/varga/billing-api.git", "base_branch": "main", "work_branch": "w"}


# ───────────────────────── the spec ─────────────────────────


def test_a_version_that_declares_no_workspace_keeps_its_digest_and_writes() -> None:
    """Absent, `workspace` is left out of the stored spec: no published version's digest moves."""
    from choregos_api.schemas.agents import AgentSpec
    from choregos_api.services.agents import empreinte

    legacy = AgentSpec.model_validate(LEGACY_SPEC)
    assert "workspace" not in legacy.model_dump(mode="json")
    assert empreinte(legacy) == LEGACY_DIGEST
    assert legacy.effective_workspace() == "write", "today's behaviour"
    declared = AgentSpec.model_validate({**LEGACY_SPEC, "workspace": "none"})
    assert declared.model_dump(mode="json")["workspace"] == "none"
    assert empreinte(declared) != LEGACY_DIGEST, "a declared workspace is part of what the version IS"
    assert declared.effective_workspace() == "none"


def test_a_remote_agent_gets_no_workspace_and_says_none() -> None:
    from choregos_api.schemas.agents import AgentSpec

    assert AgentSpec.model_validate({"runtime": A2A}).effective_workspace() == "none"
    with pytest.raises(ValidationError, match="no workspace"):
        AgentSpec.model_validate({"runtime": A2A, "workspace": "none"})


def test_a_hosted_agent_says_its_workspace_once() -> None:
    from choregos_api.schemas.agents import AgentSpec

    runtime = {**HOSTED, "workspace": "read"}
    assert AgentSpec.model_validate({"runtime": runtime}).effective_workspace() == "read"
    assert AgentSpec.model_validate({"runtime": runtime, "workspace": "read"}).effective_workspace() == "read"
    with pytest.raises(ValidationError, match="disagree"):
        AgentSpec.model_validate({"runtime": runtime, "workspace": "write"})


async def test_the_registry_publishes_the_workspace_and_refuses_a_bad_one(
    client: AsyncClient, admin: str
) -> None:
    spec = {"instructions": "Analyse the ticket.", "workspace": "none"}
    created = await client.post(AGENTS, json={"slug": "analyst", "display_name": "A", "spec": spec})
    assert created.status_code == 201, created.text
    version = (await client.get(f"{AGENTS}/analyst/versions/1")).json()
    assert version["spec"]["workspace"] == "none"
    refused = await client.post(
        AGENTS, json={"slug": "lister", "display_name": "L", "spec": {"workspace": "everything"}}
    )
    assert refused.status_code == 422


# ───────────────────────── the egress proxy ─────────────────────────


@pytest.mark.parametrize(
    ("stage_input", "implied"),
    [
        ({"repo": None}, []),
        ({"repo": REPO}, ["github.com"]),
        (None, ["github.com"]),
    ],
    ids=["never-cloned", "cloned", "not-prepared-yet"],
)
async def test_the_proxy_opens_the_repository_host_only_to_a_run_that_cloned_it(
    client: AsyncClient, project: dict[str, Any], stage_input: dict[str, Any] | None, implied: list[str]
) -> None:
    await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(["pypi.org"])})
    headers = await _run(project, "run-ws-1", stage_input=stage_input)
    body = (await client.get("/api/v1/internal/runs/run-ws-1/network", headers=headers)).json()
    assert body["implied"] == implied
    assert body["allow"] == ["pypi.org"]


# ───────────────────────── the scope ─────────────────────────


async def test_a_read_only_run_never_widens_its_scope(client: AsyncClient, project: dict[str, Any]) -> None:
    """The path the policy grants automatically to a writing run is refused to a reading one."""
    reader = await _run(project, "run-ws-read", allowed_paths=[".choregos/outputs/**"])
    writer = await _run(project, "run-ws-write", allowed_paths=["src/orders/**"])
    ask = {"paths": ["src/shared/util.py"], "justification": "a shared helper"}

    refused = await client.post("/api/v1/internal/runs/run-ws-read/scope-change", headers=reader, json=ask)
    assert refused.status_code == 200, refused.text
    assert refused.json()["decision"] == "denied"
    assert "read-only workspace" in refused.json()["reason"]
    assert refused.json()["allowed_paths"] == [".choregos/outputs/**"]

    granted = await client.post("/api/v1/internal/runs/run-ws-write/scope-change", headers=writer, json=ask)
    assert granted.json()["decision"] == "granted", "the same request, from a writing run"
