"""The batch adapters (S4-09, ADR 0190): the gateway's OpenAI-shaped wire, and the fake that stands for it."""

from __future__ import annotations

import json

import httpx
import pytest
from tranibor_adapters.batch import BatchError, BatchRequest, GatewayBatches, json_text
from tranibor_adapters.fakes.batch import FakeBatches

REQUESTS = [
    BatchRequest("part-0", "sys", "=== DOCUMENT: a.md | A ===\nx", 100),
    BatchRequest("part-1", "s", "u", 5),
]
BATCH = {
    "id": "batch_1",
    "status": "completed",
    "output_file_id": "file_out",
    "error_file_id": "file_err",
    "request_counts": {"total": 2, "completed": 1, "failed": 1},
}


def ok(custom_id: str, text: str) -> dict:
    body = {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }
    return {"custom_id": custom_id, "response": {"status_code": 200, "body": body}, "error": None}


def backend(seen: list[httpx.Request] | None = None, batch: dict | None = None) -> GatewayBatches:
    def reply(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/files":
            return httpx.Response(200, json={"id": "file_in"})
        if request.method == "POST" and path == "/v1/batches":
            return httpx.Response(200, json={"id": "batch_1", "status": "validating"})
        if path == "/v1/files/file_out/content":
            return httpx.Response(200, text=json.dumps(ok("part-0", '{"principles": []}')) + "\n")
        if path == "/v1/files/file_err/content":
            row = {"custom_id": "part-1", "response": None, "error": {"code": "x", "message": "overloaded"}}
            return httpx.Response(200, text=json.dumps(row) + "\n")
        if path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "batch_1", "status": "cancelling"})
        return httpx.Response(200, json=batch or BATCH)

    return GatewayBatches("http://gw/", "sk-run", transport=httpx.MockTransport(reply))


async def test_submit_uploads_the_alias_rows_then_creates_the_batch_with_the_gateway_key() -> None:
    seen: list[httpx.Request] = []
    batch_id = await backend(seen=seen).submit("platform/standard", REQUESTS)
    assert batch_id == "batch_1"
    upload, create = seen
    assert upload.headers["authorization"] == "Bearer sk-run" and str(upload.url) == "http://gw/v1/files"
    content = upload.content.decode()
    assert 'name="purpose"' in content and "batch" in content
    assert 'name="target_model_names"' in content and "platform/standard" in content
    rows = [json.loads(line) for line in content.splitlines() if line.startswith('{"custom_id"')]
    assert [r["custom_id"] for r in rows] == ["part-0", "part-1"]
    assert rows[0]["method"] == "POST" and rows[0]["url"] == "/v1/chat/completions"
    assert rows[0]["body"]["model"] == "platform/standard" and rows[0]["body"]["max_tokens"] == 100
    assert rows[0]["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert json.loads(create.content) == {
        "input_file_id": "file_in",
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
    }


async def test_poll_ends_on_a_terminal_status_and_waits_for_the_output_file() -> None:
    assert (await backend().poll("batch_1")).state == "ended"
    assert (await backend(batch={"status": "in_progress"}).poll("b")).state == "in_progress"
    no_output = {"status": "completed", "request_counts": {"total": 1, "completed": 1}}
    assert (await backend(batch=no_output).poll("b")).state == "in_progress"
    all_failed = {"status": "completed", "request_counts": {"total": 1, "completed": 0, "failed": 1}}
    assert (await backend(batch=all_failed).poll("b")).state == "ended"
    assert (await backend(batch={"status": "failed"}).poll("b")).state == "ended"


async def test_results_come_keyed_by_custom_id_from_the_output_and_the_error_file() -> None:
    results = {r.custom_id: r for r in await backend().results("batch_1")}
    assert results["part-0"].status == "succeeded" and results["part-0"].text == '{"principles": []}'
    assert results["part-0"].usage == {"input_tokens": 10, "output_tokens": 4}
    assert results["part-1"].status == "errored" and results["part-1"].error == "overloaded"
    assert await backend(batch={"status": "failed"}).results("b") == []


async def test_a_refusal_and_a_missing_key_are_batch_errors() -> None:
    refusing = GatewayBatches(
        "http://gw", "k", transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"}))
    )
    with pytest.raises(BatchError) as caught:
        await refusing.submit("m", REQUESTS)
    assert caught.value.status == 400
    with pytest.raises(BatchError, match="no gateway key"):
        await GatewayBatches("http://gw", "").poll("x")
    with pytest.raises(BatchError, match="1 to"):
        await backend().submit("m", [])


async def test_cancel_posts_to_the_cancel_endpoint() -> None:
    seen: list[httpx.Request] = []
    await backend(seen=seen).cancel("batch_1")
    assert (seen[0].method, seen[0].url.path) == ("POST", "/v1/batches/batch_1/cancel")


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
