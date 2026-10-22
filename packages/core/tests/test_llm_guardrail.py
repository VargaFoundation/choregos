"""The gateway's LLM guard masks secrets before the provider call, for a marked key only (S40-21).

ADR 0084.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
from choregos_core import llm_guardrail, redaction
from choregos_core.gates import SECRET_PATTERNS
from choregos_core.llm_guardrail import (
    GUARD_METADATA_KEY,
    GUARD_REDACT,
    REDACTED,
    ChoregosGuardrail,
    guard_request,
    mask,
)

# Fake secrets are assembled at run time: gitleaks reads every branch (see `test_redaction.py`).
_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_GH = "ghs_" + "x1" * 15
_ANT = "sk-ant-" + "api03-" + "a" * 26
_BEARER_VALUE = "abc.def.ghi-123456"
_PASSWORD = "hunter2-hunter2"


class _Key:
    """What LiteLLM hands the hook: the caller's key, with the metadata it was minted with."""

    def __init__(self, metadata: dict[str, Any] | None) -> None:
        self.metadata = metadata


async def _hook(data: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    guard = ChoregosGuardrail(guardrail_name="choregos-secrets", event_hook="pre_call", default_on=True)
    result: dict[str, Any] = await guard.async_pre_call_hook(_Key(metadata), None, data, "completion")
    return result


def _anthropic_request() -> dict[str, Any]:
    return {
        "model": "platform/standard",
        "system": [{"type": "text", "text": f"Deploy with {_ANT}."}],
        "messages": [
            {"role": "user", "content": f"The config holds {_AWS}; fix it."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": f"curl -H 'Authorization: Bearer {_BEARER_VALUE}'",
                    },
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _AWS}},
                ],
            },
        ],
    }


def _openai_request() -> dict[str, Any]:
    return {
        "model": "platform/standard",
        "messages": [
            {"role": "system", "content": "You are a coder."},
            {
                "role": "user",
                "content": [{"type": "text", "text": f"git clone https://bot:{_PASSWORD}@git.example/a.git"}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": f"GITHUB_TOKEN={_GH}"},
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_GH}"}}],
            },
        ],
    }


@pytest.mark.asyncio
async def test_a_marked_key_has_its_secrets_masked_before_the_provider_anthropic_shape() -> None:
    sent = await _hook(_anthropic_request(), {"run_id": "r1", GUARD_METADATA_KEY: GUARD_REDACT})
    assert _ANT not in repr(sent["system"]) and _BEARER_VALUE not in repr(sent["messages"])
    assert sent["messages"][0]["content"] == f"The config holds {REDACTED}; fix it."
    assert "Bearer [REDACTED]" in sent["messages"][1]["content"][0]["content"]
    assert sent["messages"][1]["content"][1]["source"]["data"] == _AWS, "media is not text: left alone"


@pytest.mark.asyncio
async def test_a_marked_key_has_its_secrets_masked_before_the_provider_openai_shape() -> None:
    sent = await _hook(_openai_request(), {GUARD_METADATA_KEY: GUARD_REDACT})
    assert sent["messages"][1]["content"][0]["text"] == f"git clone https://{REDACTED}@git.example/a.git"
    assert sent["messages"][2]["content"] == f"GITHUB_TOKEN={REDACTED}"
    assert sent["messages"][3]["content"][0]["image_url"]["url"].endswith(_GH)
    assert sent["messages"][0]["content"] == "You are a coder."


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {}, {"run_id": "r1"}, {GUARD_METADATA_KEY: "off"}])
async def test_an_unmarked_key_reaches_the_provider_unchanged(metadata: dict[str, Any] | None) -> None:
    assert await _hook(_anthropic_request(), metadata) == _anthropic_request()


@pytest.mark.asyncio
async def test_the_request_s_own_metadata_cannot_turn_the_guard_on_or_off() -> None:
    """The agent writes the request, never its key: only the key's metadata counts."""
    request = {**_openai_request(), "metadata": {GUARD_METADATA_KEY: GUARD_REDACT}}
    assert await _hook(dict(request), None) == request
    request = {**_openai_request(), "metadata": {GUARD_METADATA_KEY: "off"}}
    sent = await _hook(request, {GUARD_METADATA_KEY: GUARD_REDACT})
    assert sent["messages"][2]["content"] == f"GITHUB_TOKEN={REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        "password = getpass()",
        "Use a basic example, then bearer tokens for the API.",
        "postgresql://{user}:{password}@{host}/db",
        "url = f'https://{user}:$(TOKEN)@host'",
        "tokens_in: 1200, tokens_out: 300",
    ],
)
def test_code_and_prose_that_hold_no_secret_reach_the_model_intact(text: str) -> None:
    assert mask(text) == (text, 0)


def test_the_count_is_what_was_masked_and_the_responses_shape_is_covered() -> None:
    request: dict[str, Any] = {
        "input": [{"role": "user", "content": f"{_AWS} and {_GH}"}],
        "instructions": _ANT,
    }
    assert guard_request(request) == 3
    assert request["instructions"] == REDACTED


def test_the_known_shapes_are_the_no_secrets_gate_s() -> None:
    """The guard is stdlib-only (LiteLLM's image has no Choregos package), so it copies the shapes."""
    assert tuple(pattern for _, pattern in SECRET_PATTERNS) == llm_guardrail.KNOWN_SHAPES
    assert [p.pattern for p in redaction._KNOWN_SHAPES] == list(llm_guardrail.KNOWN_SHAPES)


def test_the_guard_imports_nothing_but_the_standard_library() -> None:
    source = pathlib.Path(llm_guardrail.__file__).read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import) ([a-z_.]+)", source, flags=re.MULTILINE))
    assert imported <= {"__future__", "importlib", "logging", "re", "collections.abc", "typing"}, imported
