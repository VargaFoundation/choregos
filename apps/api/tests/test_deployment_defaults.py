"""A new project inherits what its deployment can run (#245).

On 06/10, on the dev environment, the first agent of the HR template never reached the model: a new
project started on `claude-code` and no profile, which the dev environment does not serve (it serves
opencode behind the platform's gateway). The deployment now states its backend and its profiles; a
project that is born without mentioning them inherits them, a project that mentions them keeps its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DEFAULT_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("CHOREGOS_ALLOWED_AGENT_BACKENDS", '["opencode", "codex"]')
    monkeypatch.setenv("CHOREGOS_DEFAULT_MODEL_PROFILES", '{"standard": "platform/standard"}')
    reset_settings_cache()
    try:
        yield
    finally:
        monkeypatch.delenv("CHOREGOS_DEFAULT_AGENT_BACKEND")
        monkeypatch.delenv("CHOREGOS_ALLOWED_AGENT_BACKENDS")
        monkeypatch.delenv("CHOREGOS_DEFAULT_MODEL_PROFILES")
        reset_settings_cache()


async def _project(client: AsyncClient, slug: str, **config: Any) -> dict[str, Any]:
    body = {"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga", **config}}
    created = await client.post(f"{ORG}/projects", json=body)
    assert created.status_code == 201, created.text
    return dict((await client.get(f"/api/v1/projects/{created.json()['id']}")).json()["config"])


async def test_a_new_project_inherits_the_deployments_backend_and_profiles(
    client: AsyncClient, admin: str, deployment: None
) -> None:
    config = await _project(client, "hr")
    assert config["agent"] == {"default_backend": "opencode", "allowed_backends": ["opencode", "codex"]}
    assert config["models"]["profiles"]["standard"]["litellm_model"] == "platform/standard"


async def test_a_project_that_speaks_of_them_keeps_its_own(
    client: AsyncClient, admin: str, deployment: None
) -> None:
    config = await _project(
        client,
        "billing",
        agent={"default_backend": "claude-code", "allowed_backends": ["claude-code"]},
        models={"profiles": {"standard": "anthropic/claude-sonnet-5"}},
    )
    assert config["agent"]["default_backend"] == "claude-code"
    assert config["models"]["profiles"]["standard"]["litellm_model"] == "anthropic/claude-sonnet-5"


async def test_without_a_deployment_default_nothing_changes(client: AsyncClient, admin: str) -> None:
    config = await _project(client, "old")
    assert config["agent"]["default_backend"] == "claude-code"
    assert config["models"]["profiles"] == {}


async def test_a_lone_backend_is_also_the_only_one_allowed(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DEFAULT_AGENT_BACKEND", "opencode")
    reset_settings_cache()
    try:
        config = await _project(client, "alone")
    finally:
        monkeypatch.delenv("CHOREGOS_DEFAULT_AGENT_BACKEND")
        reset_settings_cache()
    assert config["agent"] == {"default_backend": "opencode", "allowed_backends": ["opencode"]}
    assert config["models"]["profiles"] == {}, "no profile said: no profile set"
