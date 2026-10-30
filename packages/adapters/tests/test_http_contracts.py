"""The adapters against their API, request by request — with a simulated transport.

Thirteen adapters out of nineteen had no test (inventory of 2026-09-24); LiteLLM's spend cap was
only checked against `fakes/gateway.py`, which reimplements its own logic: the fake validated the
fake. Here, what goes out on the wire is what is checked: path, headers, body — and what is done
with the answers that annoy.
"""

from __future__ import annotations

import json

import pytest
import respx
from choregos_adapters.errors import UpstreamError
from choregos_adapters.gateway.litellm import LiteLlmGateway
from choregos_adapters.notify.slack import SlackNotifier
from choregos_core.domain import Message
from httpx import Response

LITELLM = "http://litellm.test"


@respx.mock
async def test_litellm_mint_key_sends_the_cap_and_the_run_alias() -> None:
    route = respx.post(f"{LITELLM}/key/generate").mock(
        return_value=Response(200, json={"key": "sk-run", "token": "tok-1"})
    )
    gateway = LiteLlmGateway(LITELLM, "sk-master")
    key = await gateway.mint_key(
        {"run_id": "r-1", "project": "p"}, budget_usd=2.5, ttl_s=900, models=["platform/standard"]
    )
    body = json.loads(route.calls[0].request.content)
    assert body["max_budget"] == 2.5, "the HARD cap goes to LiteLLM: it cuts off, not us"
    assert body["duration"] == "15m" and body["models"] == ["platform/standard"]
    assert body["key_alias"] == "run-r-1" and "tags" not in body, "tags are Enterprise: never by default"
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-master"
    assert key.key == "sk-run" and key.key_id == "tok-1" and key.budget_usd == 2.5


@respx.mock
async def test_litellm_a_taken_alias_does_not_block_a_run_being_resumed() -> None:
    """`key_alias` is unique for life at LiteLLM; a replayed run keeps its id — retry without an alias."""
    route = respx.post(f"{LITELLM}/key/generate").mock(
        side_effect=[
            Response(400, json={"error": "key_alias already exists"}),
            Response(200, json={"key": "sk-2"}),
        ]
    )
    key = await LiteLlmGateway(LITELLM, "sk").mint_key({"run_id": "r-1"}, 1.0, 60, ["m"])
    assert key.key == "sk-2"
    assert "key_alias" not in json.loads(route.calls[1].request.content)


@respx.mock
async def test_litellm_reads_cache_reads_where_its_spend_logs_write_them() -> None:
    """The shape of a real LiteLLM spend log (dev, 2026-10-19): the cache counts live in
    `metadata.usage_object`. Read at the top of the entry, every run's cache reads were 0 (S37-16)."""
    respx.get(f"{LITELLM}/key/info").mock(return_value=Response(200, json={"info": {"spend": 0.01}}))
    anthropic_usage = {
        "prompt_tokens": 50724,
        "completion_tokens": 233,
        "cache_read_input_tokens": 50453,
        "cache_creation_input_tokens": 269,
    }
    openai_usage = {"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 800}}
    respx.get(f"{LITELLM}/spend/logs").mock(
        return_value=Response(
            200,
            json=[
                {
                    "model": "anthropic/choregos-claude-sonnet",
                    "prompt_tokens": 50724,
                    "completion_tokens": 233,
                    "spend": 0.00233,
                    "metadata": {"usage_object": anthropic_usage},
                },
                {
                    "model": "openai/gpt",
                    "prompt_tokens": 900,
                    "completion_tokens": 10,
                    "spend": 0.001,
                    "metadata": {"usage_object": openai_usage},
                },
                {"model": "openai/gpt", "prompt_tokens": 5, "completion_tokens": 1, "spend": 0.0},
            ],
        )
    )
    spend = await LiteLlmGateway(LITELLM, "sk").spend("tok-1")
    assert (spend.tokens_in, spend.tokens_cached) == (51629, 51253)


@respx.mock
async def test_litellm_the_spend_comes_from_the_key_and_the_logs() -> None:
    respx.get(f"{LITELLM}/key/info").mock(return_value=Response(200, json={"info": {"spend": 0.42}}))
    respx.get(f"{LITELLM}/spend/logs").mock(
        return_value=Response(
            200,
            json=[
                {"model": "claude-sonnet-5", "prompt_tokens": 100, "completion_tokens": 20, "spend": 0.3},
                {
                    "model": "claude-haiku-4-5",
                    "prompt_tokens": 50,
                    "completion_tokens": 5,
                    "spend": 0.12,
                    "cache_read_input_tokens": 10,
                },
            ],
        )
    )
    spend = await LiteLlmGateway(LITELLM, "sk").spend("tok-1")
    assert (spend.cost_usd, spend.tokens_in, spend.tokens_out, spend.tokens_cached) == (
        0.42,
        150,
        25,
        10,
    )
    assert spend.requests == 2 and spend.models_used == ["claude-haiku-4-5", "claude-sonnet-5"]


@respx.mock
async def test_litellm_revoking_a_key_that_is_already_gone_is_a_success() -> None:
    respx.post(f"{LITELLM}/key/delete").mock(return_value=Response(404, json={"error": "not found"}))
    await LiteLlmGateway(LITELLM, "sk").revoke("tok-old")  # does not raise
    respx.post(f"{LITELLM}/key/delete").mock(return_value=Response(500, text="boom"))
    with pytest.raises(UpstreamError):
        await LiteLlmGateway(LITELLM, "sk").revoke("tok-1")


@respx.mock
async def test_litellm_the_catalogue_carries_prices_per_thousand() -> None:
    respx.get(f"{LITELLM}/model/info").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "platform/standard",
                        "litellm_params": {"model": "anthropic/claude-sonnet-5"},
                        "model_info": {
                            "input_cost_per_token": 0.000003,
                            "output_cost_per_token": 0.000015,
                            "max_input_tokens": 200000,
                        },
                    }
                ]
            },
        )
    )
    [model] = await LiteLlmGateway(LITELLM, "sk").list_models()
    assert model.provider == "anthropic" and model.litellm_model == "anthropic/claude-sonnet-5"
    assert (model.input_cost_per_1k, model.output_cost_per_1k) == (0.003, 0.015)


@respx.mock
async def test_litellm_a_realtime_model_carries_its_audio_and_cached_prices() -> None:
    """What a voice session is measured at (S41-15): LiteLLM's audio and cache-read prices."""
    respx.get(f"{LITELLM}/model/info").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "openai/gpt-realtime",
                        "litellm_params": {"model": "openai/gpt-realtime"},
                        "model_info": {
                            "input_cost_per_token": 0.000004,
                            "output_cost_per_token": 0.000016,
                            "input_cost_per_audio_token": 0.000032,
                            "output_cost_per_audio_token": 0.000064,
                            "cache_read_input_token_cost": 0.0000004,
                        },
                    }
                ]
            },
        )
    )
    [model] = await LiteLlmGateway(LITELLM, "sk").list_models()
    assert (model.input_audio_cost_per_1k, model.output_audio_cost_per_1k) == (0.032, 0.064)
    assert model.cached_input_cost_per_1k == 0.0004


@respx.mock
async def test_litellm_an_unknown_tool_calling_capability_is_not_a_denial() -> None:
    """What the dev tenant's LiteLLM answers for its `openai/choregos-*` aliases (S22-42)."""
    respx.get(f"{LITELLM}/model/info").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "platform/standard",
                        "litellm_params": {"model": "openai/choregos-standard"},
                        "model_info": {"supports_function_calling": None},
                    },
                    {
                        "model_name": "platform/embed",
                        "litellm_params": {"model": "openai/choregos-embed"},
                        "model_info": {"supports_function_calling": False},
                    },
                ]
            },
        )
    )
    standard, embed = await LiteLlmGateway(LITELLM, "sk").list_models()
    assert standard.supports_tool_calling, "an unknown capability must not refuse every stage"
    assert not embed.supports_tool_calling, "an explicit false still denies it"


@respx.mock
async def test_slack_webhook_then_bot_and_their_refusals() -> None:
    hook = respx.post("https://hooks.slack.test/x").mock(return_value=Response(200, text="ok"))
    message = Message(title="Train frozen", body="reason", severity="warning")
    await SlackNotifier(webhook_url="https://hooks.slack.test/x").send("#ops", message)
    body = json.loads(hook.calls[0].request.content)
    assert body["text"] == "Train frozen" and body["blocks"][0]["type"] == "header"

    bot = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    with pytest.raises(UpstreamError, match="channel_not_found"):
        await SlackNotifier(bot_token="xoxb").send("#nowhere", message)
    assert bot.calls[0].request.headers["Authorization"] == "Bearer xoxb"

    with pytest.raises(UpstreamError, match="neither `bot_token` nor `webhook_url`"):
        await SlackNotifier().send("#ops", message)


# ───────────────────────── Ecphoria ─────────────────────────

ECPHORIA = "http://ecphoria.test"


@respx.mock
async def test_ecphoria_the_tenant_and_the_token_go_in_the_headers() -> None:
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    route = respx.post(f"{ECPHORIA}/api/v1/context-pack").mock(
        return_value=Response(
            200,
            json={
                "tokens_estimated": 120,
                "truncated": False,
                "memories": [],
                "incidents": [],
                "related_items": [{"key": "D-3", "title": "A linked ticket", "state": "done"}],
            },
        )
    )
    memory = EcphoriaMemory(ECPHORIA, token="t-secret", read_timeout_ms=500)
    pack = await memory.context_pack("basket", "VAT of the basket", ["src/**"], budget_tokens=2000)
    request = route.calls[0].request
    assert request.headers["X-Ecphoria-Tenant"] == "basket", "one tenant per project: the isolation is there"
    assert request.headers["Authorization"] == "Bearer t-secret"
    body = json.loads(request.content)
    assert (
        body["query"] == "VAT of the basket" and body["budget_tokens"] == 2000 and body["paths"] == ["src/**"]
    )
    assert pack.tokens_estimated == 120 and pack.related_items[0].key == "D-3"


@respx.mock
async def test_ecphoria_an_outage_returns_an_empty_pack_then_opens_the_circuit() -> None:
    """Any outage returns an EMPTY pack, never an error; after three, stop waiting."""
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    route = respx.post(f"{ECPHORIA}/api/v1/context-pack").mock(return_value=Response(503, text="quorum lost"))
    memory = EcphoriaMemory(ECPHORIA, read_timeout_ms=200)
    for _ in range(3):
        pack = await memory.context_pack("p", "q", [], budget_tokens=100)
        assert pack.memories == [] and pack.query == "q"
    assert route.call_count == 3
    await memory.context_pack("p", "q", [], budget_tokens=100)
    assert route.call_count == 3, "circuit open: no more calls, no more waiting"


@respx.mock
async def test_ecphoria_the_search_reads_the_hits_with_their_score() -> None:
    from choregos_adapters.memory.ecphoria import EcphoriaMemory

    respx.post(f"{ECPHORIA}/api/v1/memories/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "memory": {
                            "id": "m-1",
                            "content": "Credit notes are subtracted from the total.",
                            "kind": "decision",
                        },
                        "score": 0.91,
                    }
                ]
            },
        )
    )
    [hit] = await EcphoriaMemory(ECPHORIA).search("basket", "credit notes", k=5)
    assert hit.id == "m-1" and "credit notes" in hit.content.lower()


# ───────────────────────── Argo CD ─────────────────────────

ARGO = "http://argocd.test"


@respx.mock
async def test_argocd_health_revision_and_unknown_application() -> None:
    from choregos_adapters.cd.argocd import ArgoCdAdapter

    respx.get(f"{ARGO}/api/v1/applications/billing-api-prod").mock(
        return_value=Response(
            200,
            json={
                "status": {
                    "health": {"status": "Degraded", "message": "1/3 pods"},
                    "sync": {"revision": "abc123"},
                }
            },
        )
    )
    respx.get(f"{ARGO}/api/v1/applications/nowhere").mock(return_value=Response(404, text="not found"))
    argo = ArgoCdAdapter(base_url=ARGO, token="t")
    health = await argo.health("billing-api-prod")
    assert (health.status, health.message, health.revision) == ("Degraded", "1/3 pods", "abc123")
    assert await argo.current_revision("billing-api-prod") == "abc123"
    assert (await argo.health("nowhere")).status == "Missing"
    assert await argo.current_revision("nowhere") == "unknown"


@respx.mock
async def test_argocd_an_aborted_rollout_reads_from_its_conditions() -> None:
    from choregos_adapters.cd.argocd import ArgoCdAdapter

    manifest = {
        "spec": {"strategy": {"canary": {"steps": [{"setWeight": 10}, {"pause": {}}, {"setWeight": 50}]}}},
        "status": {
            "phase": "Paused",
            "currentStepIndex": 1,
            "canary": {"weight": 10},
            "conditions": [{"reason": "RolloutAborted"}],
            "message": "SLO analysis failed",
        },
    }
    respx.get(f"{ARGO}/api/v1/applications/billing-api-prod/resource").mock(
        return_value=Response(200, json={"manifest": json.dumps(manifest)})
    )
    rollout = await ArgoCdAdapter(base_url=ARGO).rollout_status("billing-api-prod")
    assert (
        rollout.phase == "Aborted"
        and rollout.current_step == 1
        and rollout.total_steps == 3
        and rollout.canary_weight == 10
    )
    assert "SLO" in rollout.message

    abort = respx.post(f"{ARGO}/api/v1/applications/billing-api-prod/rollback").mock(
        return_value=Response(200, json={})
    )
    await ArgoCdAdapter(base_url=ARGO).abort_rollout("billing-api-prod")
    assert json.loads(abort.calls[0].request.content) == {"name": "billing-api-prod", "prune": False}
