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


def model_info(*deployments: tuple[str, str]) -> dict:
    """`GET /v1/model/info` as LiteLLM v1.105.5 answers: a row per deployment, its `litellm_params.model`."""
    return {
        "data": [
            {"model_name": n, "litellm_params": {"model": m}, "model_info": {"id": "x"}}
            for n, m in deployments
        ]
    }


INFO = model_info(("platform/standard", "openai/gpt-5.5"), ("platform/fast", "openai/gpt-5.5-mini"))


def backend(
    seen: list[httpx.Request] | None = None, batch: dict | None = None, info: dict | None = None
) -> GatewayBatches:
    def reply(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/v1/model/info":
            return httpx.Response(200, json=info or INFO)
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


async def test_submit_uploads_the_upstream_rows_then_creates_the_batch_with_the_gateway_key() -> None:
    seen: list[httpx.Request] = []
    batch_id = await backend(seen=seen).submit("platform/standard", REQUESTS)
    assert batch_id == "batch_1"
    lookup, upload, create = seen
    assert (lookup.method, str(lookup.url)) == ("GET", "http://gw/v1/model/info")
    assert lookup.headers["authorization"] == "Bearer sk-run"
    assert upload.headers["authorization"] == "Bearer sk-run" and str(upload.url) == "http://gw/v1/files"
    content = upload.content.decode()
    assert 'name="purpose"' in content and "batch" in content
    # The open path (no litellm_enterprise): the alias rides a header, never `target_model_names`.
    assert upload.headers["x-litellm-model"] == "platform/standard"
    assert "target_model_names" not in content
    rows = [json.loads(line) for line in content.splitlines() if line.startswith('{"custom_id"')]
    assert [r["custom_id"] for r in rows] == ["part-0", "part-1"]
    assert rows[0]["method"] == "POST" and rows[0]["url"] == "/v1/chat/completions"
    assert rows[0]["body"]["model"] == "gpt-5.5" and rows[0]["body"]["max_tokens"] == 100
    assert rows[0]["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert json.loads(create.content) == {
        "input_file_id": "file_in",
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
    }


async def test_the_alias_is_resolved_once_per_adapter_and_each_alias_to_its_own_model() -> None:
    seen: list[httpx.Request] = []
    gateway = backend(seen=seen)
    assert await gateway.upstream_model("platform/standard") == "gpt-5.5"
    assert await gateway.upstream_model("platform/fast") == "gpt-5.5-mini"
    await gateway.submit("platform/standard", REQUESTS)
    await gateway.submit("platform/standard", REQUESTS)
    assert [r.url.path for r in seen].count("/v1/model/info") == 2, "one lookup per alias, then kept"


async def test_the_ids_the_gateway_encodes_with_the_alias_are_sent_back_untouched() -> None:
    """On the model path LiteLLM answers `file-<base64>` / `batch_<base64>` ids that carry the alias."""
    file_id, batch_id, out_id = "file-bGl0ZWxsbTpm", "batch_bGl0ZWxsbTpi", "file-bGl0ZWxsbTpv"

    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/model/info":
            return httpx.Response(200, json=INFO)
        if request.method == "POST" and path == "/v1/files":
            return httpx.Response(200, json={"id": file_id})
        if request.method == "POST" and path == "/v1/batches":
            return httpx.Response(200, json={"id": batch_id})
        if path == f"/v1/batches/{batch_id}":
            return httpx.Response(
                200,
                json={"id": batch_id, "status": "completed", "output_file_id": out_id, "request_counts": {}},
            )
        if path == f"/v1/files/{out_id}/content":
            return httpx.Response(200, text=json.dumps(ok("part-0", "{}")) + "\n")
        return httpx.Response(200, json={"id": batch_id})

    seen: list[httpx.Request] = []
    gateway = GatewayBatches(
        "http://gw", "k", transport=httpx.MockTransport(lambda r: (seen.append(r), reply(r))[1])
    )
    assert await gateway.submit("platform/standard", REQUESTS) == batch_id
    assert json.loads(seen[-1].content)["input_file_id"] == file_id
    assert [r.custom_id for r in await gateway.results(batch_id)] == ["part-0"]
    await gateway.cancel(batch_id)
    assert seen[-1].url.path == f"/v1/batches/{batch_id}/cancel"
    assert "x-litellm-model" not in seen[-1].headers, (
        "only the upload names the alias: the ids carry it after"
    )


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (model_info(("other", "openai/gpt-5.5")), "lists no model"),
        (model_info(("a", "openai/gpt-5.5"), ("a", "openai/gpt-5.5-mini")), "several upstream models"),
        (model_info(("a", "anthropic/claude-sonnet-5")), "only OpenAI"),
        ({"data": [{"model_name": "a", "litellm_params": {}}]}, "does not say"),
    ],
)
async def test_an_alias_without_a_single_openai_model_is_refused_before_any_upload(
    info: dict, message: str
) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(BatchError, match=message):
        await backend(seen=seen, info=info).submit("a", REQUESTS)
    assert [r.url.path for r in seen] == ["/v1/model/info"], "nothing was uploaded"


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
