"""An agent written in code (ADR 0063): its image is a name the operator mapped, listed by name only."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

AGENTS = "/api/v1/orgs/varga/agents"
REFERENCE = "registry.example/agents/ticket-triager@sha256:" + "d" * 64


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_AGENT_IMAGES", json.dumps({"ticket-triager": REFERENCE}))
    reset_settings_cache()


def _spec(runtime: dict[str, Any] | None, backend: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"instructions": "Triage {{ ticket.title }}."}
    if runtime is not None:
        spec["runtime"] = runtime
    if backend is not None:
        spec["backend"] = backend
    return spec


def _code(image: str = "ticket-triager") -> dict[str, Any]:
    return {"kind": "code", "image": image, "command": ["python", "-m", "ticket_triager"]}


async def _create(client: AsyncClient, spec: dict[str, Any]) -> Any:
    return await client.post(AGENTS, json={"slug": "triager", "display_name": "Triager", "spec": spec})


async def test_a_code_agent_is_published_and_read_back(client: AsyncClient, admin: str, images: None) -> None:
    created = await _create(client, _spec(_code()))
    assert created.status_code == 201, created.text
    version = (await client.get(f"{AGENTS}/triager/versions/1")).json()
    assert version["spec"]["runtime"] == {**_code(), "api_format": "openai"}


async def test_an_unknown_image_is_refused_naming_the_known_ones(
    client: AsyncClient, admin: str, images: None
) -> None:
    refused = await _create(client, _spec(_code(image="miner")))
    assert refused.status_code == 422, refused.text
    error = refused.json()["errors"][0]
    assert error["loc"] == ["runtime", "image"]
    assert "miner" in error["msg"] and "ticket-triager" in error["msg"]
    assert (await client.get(f"{AGENTS}/triager")).status_code == 404


async def test_a_code_runtime_with_a_backend_is_refused(
    client: AsyncClient, admin: str, images: None
) -> None:
    refused = await _create(client, _spec(_code(), backend="codex"))
    assert refused.status_code == 422, refused.text
    assert "`code` runtime fixes the backend" in refused.text


async def test_an_image_that_is_a_reference_and_not_a_name_is_refused(
    client: AsyncClient, admin: str, images: None
) -> None:
    refused = await _create(client, _spec(_code(image=REFERENCE)))
    assert refused.status_code == 422, refused.text


async def test_a_version_without_a_runtime_is_unchanged(
    client: AsyncClient, admin: str, images: None
) -> None:
    created = await _create(client, _spec(None, backend="codex"))
    assert created.status_code == 201, created.text
    spec = (await client.get(f"{AGENTS}/triager/versions/1")).json()["spec"]
    assert spec["runtime"] is None and spec["backend"] == "codex"


async def test_the_image_names_are_listed_without_their_references(
    client: AsyncClient, admin: str, images: None
) -> None:
    listed = await client.get("/api/v1/platform/agent-images")
    assert listed.status_code == 200, listed.text
    assert listed.json() == [{"name": "ticket-triager", "digest_pinned": True}]
    assert "registry.example" not in listed.text and "sha256" not in listed.text


async def test_a_tag_only_image_is_listed_as_not_pinned(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_AGENT_IMAGES", json.dumps({"loose": "registry.example/agent:latest"}))
    reset_settings_cache()
    assert (await client.get("/api/v1/platform/agent-images")).json() == [
        {"name": "loose", "digest_pinned": False}
    ]


def test_a_tag_only_reference_stops_the_api_when_digests_are_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import Settings
    from pydantic import ValidationError

    monkeypatch.setenv("CHOREGOS_REQUIRE_IMAGE_DIGEST", "true")
    monkeypatch.setenv("CHOREGOS_AGENT_IMAGES", json.dumps({"loose": "registry.example/agent:latest"}))
    with pytest.raises(ValidationError, match="not pinned by digest"):
        Settings()
