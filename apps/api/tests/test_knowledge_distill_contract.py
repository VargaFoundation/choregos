"""Contract of the distillation in passes (S42-10b, ADR 0001): the estimate route, the optional
request body, and the `distillation` object's new members are declared in `openapi.yaml` and the DTOs
say the same thing. The behaviour behind them lands with S42-10c: until then they answer 501."""

from __future__ import annotations

import choregos_contracts as contracts
from choregos_api.schemas.knowledge import KnowledgeDistillEstimate, KnowledgeDistillRequest
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/knowledge"


def _schema(name: str) -> dict[str, object]:
    return dict(contracts.load_openapi()["components"]["schemas"][name])


def test_the_dtos_say_what_the_contract_says() -> None:
    estimate, request = _schema("KnowledgeDistillEstimate"), _schema("KnowledgeDistillRequest")
    assert set(estimate["properties"]) == set(KnowledgeDistillEstimate.model_fields)  # type: ignore[call-overload]
    assert set(estimate["required"]) <= set(KnowledgeDistillEstimate.model_fields)  # type: ignore[call-overload]
    assert set(request["properties"]) == set(KnowledgeDistillRequest.model_fields)  # type: ignore[call-overload]
    description = _schema("KnowledgeBase")["properties"]["distillation"]["description"]  # type: ignore[index]
    for word in ("too_large", "over_budget", "parts_done", "parts_total", "spent_usd", "started_at", "phase"):
        assert word in description


def test_the_estimate_and_the_optional_body_are_declared() -> None:
    paths = contracts.load_openapi()["paths"]
    estimate = paths["/orgs/{org}/knowledge/{slug}/distill/estimate"]["get"]
    assert estimate["operationId"] == "estimateKnowledgeDistillation"
    distill = paths["/orgs/{org}/knowledge/{slug}/distill"]["post"]
    assert distill["requestBody"]["required"] is False
    assert "409" in distill["responses"] and "422" in distill["responses"]


async def test_the_estimate_is_not_served_before_s42_10c(client: AsyncClient, admin: str) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    answer = await client.get(f"{BASE}/architecture/distill/estimate")
    assert answer.status_code == 501 and answer.json()["code"] == "distill_in_passes_not_implemented"
    assert (await client.get(f"{BASE}/unknown/distill/estimate")).status_code == 404


async def test_a_body_is_validated_and_never_silently_ignored(client: AsyncClient, admin: str) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    too_many = await client.post(f"{BASE}/architecture/distill", json={"exclude": ["*.xlsx"] * 21})
    assert too_many.status_code == 422
    capped = await client.post(f"{BASE}/architecture/distill", json={"max_usd": 2.5})
    assert capped.status_code == 501  # a cap nothing enforces is refused, not dropped
    assert (await client.post(f"{BASE}/architecture/distill", json={})).status_code == 202
