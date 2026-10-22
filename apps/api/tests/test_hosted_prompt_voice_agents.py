"""Prompt, voice and hosted runtimes in the registry (ADR 0080): what a version may say, and what the
platform refuses until the supply chain exists."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

AGENTS = "/api/v1/orgs/varga/agents"
DIGEST = "sha256:" + "a" * 64


def _spec(runtime: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"instructions": "Answer {{ ticket.title }}.", "runtime": runtime, **extra}


def _hosted(**extra: Any) -> dict[str, Any]:
    return {"kind": "hosted", "source": {"type": "image", "ref": "ghcr.io/acme/triager:1.2"}, **extra}


async def _create(client: AsyncClient, spec: dict[str, Any]) -> Any:
    return await client.post(AGENTS, json={"slug": "triager", "display_name": "Triager", "spec": spec})


async def test_a_prompt_agent_is_published_and_read_back(client: AsyncClient, admin: str) -> None:
    created = await _create(client, _spec({"kind": "prompt", "response_format": "json_object"}))
    assert created.status_code == 201, created.text
    runtime = (await client.get(f"{AGENTS}/triager/versions/1")).json()["spec"]["runtime"]
    assert runtime == {
        "kind": "prompt",
        "max_output_tokens": None,
        "tool_choice": "auto",
        "response_format": "json_object",
    }


async def test_a_voice_agent_is_published_with_its_audio_defaults(client: AsyncClient, admin: str) -> None:
    created = await _create(client, _spec({"kind": "voice", "voice": "verse"}, model="gpt-realtime"))
    assert created.status_code == 201, created.text
    runtime = (await client.get(f"{AGENTS}/triager/versions/1")).json()["spec"]["runtime"]
    assert runtime["audio"] == {"format": "pcm16", "sample_rate": 24000, "channels": 1}
    assert runtime["turn_detection"] == "server_vad"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (_spec({"kind": "prompt"}, backend="codex"), "`prompt` runtime runs no coding agent"),
        (_spec({"kind": "voice"}, model="gpt-realtime", backend="codex"), "`voice` runtime runs no"),
        (
            _spec({"kind": "voice"}, model="gpt-realtime", skills=[{"slug": "triage"}]),
            "`voice` runtime reads no skill",
        ),
        (_spec({"kind": "voice"}), "`model` is required"),
        (_spec(_hosted(), backend="codex"), "`hosted` runtime runs no coding agent"),
        (_spec(_hosted(), skills=[{"slug": "triage"}]), "`hosted` runtime reads no skill"),
        (_spec(_hosted(port=4002)), "loopback"),
        (_spec(_hosted(env={"OPENAI_API_KEY": "sk-x"})), "reserved"),
        (_spec(_hosted(env={"HTTPS_PROXY": "http://elsewhere"})), "reserved"),
        # S41-19: another managed-identity protocol would bypass the loopback identity.
        (_spec(_hosted(env={"IDENTITY_SERVER_THUMBPRINT": "ab"})), "reserved"),
        (_spec(_hosted(env={"IMDS_ENDPOINT": "http://169.254.169.254"})), "reserved"),
        (_spec(_hosted(env={"MSI_ENDPOINT": "http://elsewhere"})), "reserved"),
        (_spec(_hosted(env={"MSI_SECRET": "s"})), "reserved"),
        (_spec(_hosted(env={"FOUNDRY_PROJECT_ENDPOINT": "https://x.services.ai.azure.com"})), "reserved"),
        (_spec(_hosted(protocols=["responses", "responses"])), "listed once"),
        (
            _spec(
                _hosted(source={"type": "zip", "sha256": DIGEST, "runtime": "ruby_3", "entry_point": ["x"]})
            ),
            "",
        ),
    ],
)
async def test_a_version_that_contradicts_its_runtime_is_refused(
    client: AsyncClient, admin: str, spec: dict[str, Any], message: str
) -> None:
    refused = await _create(client, spec)
    assert refused.status_code == 422, refused.text
    assert message in refused.text
    assert (await client.get(f"{AGENTS}/triager")).status_code == 404


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"type": "image", "ref": "ghcr.io/acme/triager:1.2"}, "under no allowed prefix"),
        (
            {"type": "zip", "sha256": DIGEST, "runtime": "python_3_13", "entry_point": ["python", "main.py"]},
            "upload it first",
        ),
    ],
)
async def test_no_hosted_source_is_accepted_by_default(
    client: AsyncClient, admin: str, source: dict[str, Any], message: str
) -> None:
    refused = await _create(client, _spec(_hosted(source=source)))
    assert refused.status_code == 422, refused.text
    error = refused.json()["errors"][0]
    assert error["loc"][:2] == ["runtime", "source"] and message in error["msg"]
