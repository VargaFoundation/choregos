"""The gateway's LLM guard masks secrets for a marked key: in the request and in the answer (S40-21).

ADR 0084.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace
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
    assert imported <= {"__future__", "importlib", "json", "logging", "re", "collections.abc", "typing"}, (
        imported
    )


# The answer, on the way back (S40-21b). LiteLLM's objects are stood in for by namespaces: the
# hook reads attributes, and the gateway image is where the real ones are (`tests/live/`).
_ANSWER = f"Here is the key {_AWS}\nand a clone https://bot:{_PASSWORD}@git.example/a.git\nbye"
_MASKED_ANSWER = mask(_ANSWER)[0]
_ARGUMENTS = json.dumps({"path": "k.txt", "content": _GH})


def _chat_answer() -> SimpleNamespace:
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="write_file", arguments=_ARGUMENTS))
    message = SimpleNamespace(role="assistant", content=_ANSWER, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(index=0, message=message, finish_reason="tool_calls")])


def _anthropic_answer() -> dict[str, Any]:
    return {
        "type": "message",
        "content": [
            {"type": "text", "text": _ANSWER},
            {
                "type": "tool_use",
                "id": "t1",
                "name": "write_file",
                "input": {"path": "k.txt", "content": _GH},
            },
        ],
    }


async def _answer(response: Any, metadata: dict[str, Any] | None) -> Any:
    guard = ChoregosGuardrail(guardrail_name="choregos-secrets", event_hook="post_call", default_on=True)
    return await guard.async_post_call_success_hook({}, _Key(metadata), response)


def _pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _chat_stream() -> list[SimpleNamespace]:
    """The answer cut every 5 characters, so that every secret straddles chunks, then a tool call."""

    def chunk(finish: str | None = None, **delta: Any) -> SimpleNamespace:
        choice = SimpleNamespace(index=0, delta=SimpleNamespace(**delta), finish_reason=finish)
        return SimpleNamespace(choices=[choice])

    def call(arguments: str) -> SimpleNamespace:
        return SimpleNamespace(index=0, function=SimpleNamespace(arguments=arguments))

    chunks = [chunk(role="assistant", content="")]
    chunks += [chunk(content=piece) for piece in _pieces(_ANSWER, 5)]
    chunks += [chunk(content=None, tool_calls=[call(piece)]) for piece in _pieces(_ARGUMENTS, 4)]
    return [*chunks, chunk(finish="tool_calls", content=None)]


def _sse(data: dict[str, Any]) -> bytes:
    return f"event: {data['type']}\ndata: {json.dumps(data)}\n\n".encode()


def _anthropic_stream() -> list[bytes]:
    def delta(index: int, kind: str, field: str, text: str) -> bytes:
        return _sse({"type": "content_block_delta", "index": index, "delta": {"type": kind, field: text}})

    events = [_sse({"type": "message_start", "message": {"content": []}})]
    events += [
        _sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    ]
    events += [delta(0, "text_delta", "text", piece) for piece in _pieces(_ANSWER, 5)]
    events += [_sse({"type": "content_block_stop", "index": 0})]
    events += [_sse({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use"}})]
    events += [delta(1, "input_json_delta", "partial_json", piece) for piece in _pieces(_ARGUMENTS, 4)]
    events += [_sse({"type": "content_block_stop", "index": 1}), _sse({"type": "message_stop"})]
    return events


async def _streamed(chunks: list[Any], metadata: dict[str, Any] | None) -> list[Any]:
    async def upstream() -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    guard = ChoregosGuardrail(guardrail_name="choregos-secrets", event_hook="post_call", default_on=True)
    hook = guard.async_post_call_streaming_iterator_hook(_Key(metadata), upstream(), {})
    return [chunk async for chunk in hook]


def _chat_text(chunks: list[Any]) -> tuple[str, str]:
    deltas = [chunk.choices[0].delta for chunk in chunks]
    content = "".join(getattr(delta, "content", None) or "" for delta in deltas)
    calls = [call for delta in deltas for call in getattr(delta, "tool_calls", [])]
    return content, "".join(call.function.arguments for call in calls)


def _anthropic_text(chunks: list[bytes]) -> tuple[str, str]:
    deltas = [
        json.loads(line[5:])["delta"]
        for chunk in chunks
        for line in chunk.split(b"\n")
        if line.startswith(b"data:") and b"content_block_delta" in line
    ]
    return "".join(d.get("text", "") for d in deltas), "".join(d.get("partial_json", "") for d in deltas)


@pytest.mark.asyncio
async def test_a_marked_key_has_the_secrets_in_the_model_s_answer_masked_chat_shape() -> None:
    message = (await _answer(_chat_answer(), {GUARD_METADATA_KEY: GUARD_REDACT})).choices[0].message
    assert message.content == _MASKED_ANSWER
    assert _AWS not in message.content and _PASSWORD not in message.content
    assert json.loads(message.tool_calls[0].function.arguments) == {"path": "k.txt", "content": REDACTED}


@pytest.mark.asyncio
async def test_a_marked_key_has_the_secrets_in_the_model_s_answer_masked_anthropic_shape() -> None:
    answer = await _answer(_anthropic_answer(), {GUARD_METADATA_KEY: GUARD_REDACT})
    assert answer["content"][0]["text"] == _MASKED_ANSWER
    assert answer["content"][1]["input"] == {"path": "k.txt", "content": REDACTED}


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {"run_id": "r1"}, {GUARD_METADATA_KEY: "off"}])
async def test_an_unmarked_key_gets_the_answer_unchanged(metadata: dict[str, Any] | None) -> None:
    assert await _answer(_anthropic_answer(), metadata) == _anthropic_answer()
    assert _chat_text(await _streamed(_chat_stream(), metadata)) == (_ANSWER, _ARGUMENTS)
    assert await _streamed(_anthropic_stream(), metadata) == _anthropic_stream()


@pytest.mark.asyncio
async def test_a_streamed_answer_is_masked_though_each_secret_straddles_chunks_chat_shape() -> None:
    chunks = await _streamed(_chat_stream(), {GUARD_METADATA_KEY: GUARD_REDACT})
    assert len(chunks) == len(_chat_stream()), "chunks are emptied, never dropped or merged"
    content, arguments = _chat_text(chunks)
    assert content == _MASKED_ANSWER
    assert json.loads(arguments) == {"path": "k.txt", "content": REDACTED}


@pytest.mark.asyncio
async def test_a_streamed_answer_is_masked_though_each_secret_straddles_chunks_anthropic_shape() -> None:
    chunks = await _streamed(_anthropic_stream(), {GUARD_METADATA_KEY: GUARD_REDACT})
    assert len(chunks) == len(_anthropic_stream())
    text, partial_json = _anthropic_text(chunks)
    assert text == _MASKED_ANSWER
    assert json.loads(partial_json) == {"path": "k.txt", "content": REDACTED}
    assert chunks[0] == _anthropic_stream()[0], "an event no secret touched goes out byte for byte"


def test_a_streamed_answer_is_released_line_by_line_not_at_the_end() -> None:
    """Holding back is per line: the agent still sees its answer stream."""
    guard = llm_guardrail.StreamGuard()
    out: list[Any] = []
    for chunk in _chat_stream()[:12]:
        guard.feed(chunk)
        out += guard.ready()
    assert _chat_text(out)[0] == mask(_ANSWER.split("\n", maxsplit=1)[0] + "\n")[0]


def test_a_long_line_is_released_at_a_space_after_hold_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_guardrail, "HOLD_MAX", 20)
    guard = llm_guardrail.StreamGuard()
    out: list[Any] = []
    for piece in _pieces("one two three four five six seven eight nine", 4):
        delta = SimpleNamespace(content=piece)
        guard.feed(SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)]))
        out += guard.ready()
    assert _chat_text(out)[0].startswith("one two three four")
