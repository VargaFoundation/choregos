"""The batch adapters (S4-09, ADR 0190): the Anthropic wire, and the fake that stands for it."""

from __future__ import annotations

import json

import httpx
import pytest
from tranibor_adapters.batch import AnthropicBatches, BatchError, BatchRequest, json_text
from tranibor_adapters.fakes.batch import FakeBatches

REQUESTS = [
    BatchRequest("part-0", "sys", "=== DOCUMENT: a.md | A ===\nx", 100),
    BatchRequest("part-1", "s", "u", 5),
]


def backend(
    handler: httpx.MockTransport | None = None, seen: list[httpx.Request] | None = None
) -> AnthropicBatches:
    def reply(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            return httpx.Response(200, json={"id": "msgbatch_1", "processing_status": "in_progress"})
        if path.endswith("/results"):
            lines = [
                {
                    "custom_id": "part-1",
                    "result": {"type": "errored", "error": {"error": {"message": "overloaded"}}},
                },
                {
                    "custom_id": "part-0",
                    "result": {
                        "type": "succeeded",
                        "message": {
                            "content": [{"type": "text", "text": '{"principles": []}'}],
                            "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 3},
                        },
                    },
                },
            ]
            return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines) + "\n")
        if path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "msgbatch_1"})
        return httpx.Response(
            200, json={"id": "msgbatch_1", "processing_status": "ended", "request_counts": {"succeeded": 1}}
        )

    return AnthropicBatches("sk-test", transport=handler or httpx.MockTransport(reply))


async def test_submit_sends_one_request_per_custom_id_with_the_key_and_version() -> None:
    seen: list[httpx.Request] = []
    batch_id = await backend(seen=seen).submit("claude-x", REQUESTS)
    assert batch_id == "msgbatch_1"
    request = seen[0]
    assert request.headers["x-api-key"] == "sk-test" and request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert [r["custom_id"] for r in body["requests"]] == ["part-0", "part-1"]
    first = body["requests"][0]["params"]
    assert first["model"] == "claude-x" and first["max_tokens"] == 100 and first["system"] == "sys"
    assert first["messages"] == [{"role": "user", "content": REQUESTS[0].user}]


async def test_poll_reads_the_processing_status() -> None:
    status = await backend().poll("msgbatch_1")
    assert status.state == "ended" and status.counts == {"succeeded": 1}
    pending = httpx.MockTransport(lambda r: httpx.Response(200, json={"processing_status": "in_progress"}))
    assert (await backend(pending).poll("msgbatch_1")).state == "in_progress"


async def test_results_come_keyed_by_custom_id_in_whatever_order_the_provider_chose() -> None:
    results = {r.custom_id: r for r in await backend().results("msgbatch_1")}
    assert results["part-0"].status == "succeeded" and results["part-0"].text == '{"principles": []}'
    assert (
        results["part-0"].usage["input_tokens"] == 10
        and results["part-0"].usage["cache_read_input_tokens"] == 3
    )
    assert results["part-1"].status == "errored" and results["part-1"].error == "overloaded"


async def test_a_refusal_and_a_missing_key_are_batch_errors() -> None:
    refusing = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(BatchError) as caught:
        await backend(refusing).submit("m", REQUESTS)
    assert caught.value.status == 400
    with pytest.raises(BatchError, match="no Anthropic API key"):
        await AnthropicBatches("").poll("x")
    with pytest.raises(BatchError, match="1 to"):
        await backend().submit("m", [])


async def test_cancel_posts_to_the_cancel_endpoint() -> None:
    seen: list[httpx.Request] = []
    await backend(seen=seen).cancel("msgbatch_1")
    assert (seen[0].method, seen[0].url.path) == ("POST", "/v1/messages/batches/msgbatch_1/cancel")


def test_json_text_takes_the_object_with_or_without_a_fence_and_refuses_the_rest() -> None:
    assert json_text('{"a": 1}') == {"a": 1}
    assert json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert json_text('Here you go: {"a": 1}') == {"a": 1}
    assert json_text('{"a": [1, 2') is None  # cut by max_tokens
    assert json_text("[1]") is None


async def test_the_fake_ends_after_its_polls_answers_in_reverse_and_can_force_outcomes() -> None:
    fake = FakeBatches(polls_until_ended=1, outcomes={"part-1": "expired"})
    batch_id = await fake.submit("m", REQUESTS)
    assert (await fake.poll(batch_id)).state == "in_progress"
    assert (await fake.poll(batch_id)).state == "ended"
    results = await fake.results(batch_id)
    assert [r.custom_id for r in results] == ["part-1", "part-0"]
    assert [r.status for r in results] == ["expired", "succeeded"]
    assert json_text(results[1].text)["principles"][0]["sources"][0]["document"] == "a.md"
    await fake.cancel(batch_id)
    assert (await fake.poll(batch_id)).state == "ended" and fake.cancelled == [batch_id]
    with pytest.raises(BatchError):
        await FakeBatches(fail_submit=True).submit("m", REQUESTS)
