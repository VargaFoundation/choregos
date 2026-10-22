"""Contract of the distillation in passes (S42-10b, ADR 0001): the estimate route, the optional
request body, and the `distillation` object's new members are declared in `openapi.yaml` and the DTOs
say the same thing. The behaviour behind them is S42-10c (`test_knowledge_distillation_passes.py`)."""

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


async def test_the_estimate_answers_for_a_known_base_only(client: AsyncClient, admin: str) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    empty = await client.get(f"{BASE}/architecture/distill/estimate")
    assert empty.status_code == 200 and empty.json()["documents"] == 0
    assert "no converted document" in empty.json()["refused_reason"]
    assert (await client.get(f"{BASE}/unknown/distill/estimate")).status_code == 404


async def test_a_body_is_validated(client: AsyncClient, admin: str) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    too_many = await client.post(f"{BASE}/architecture/distill", json={"exclude": ["*.xlsx"] * 21})
    assert too_many.status_code == 422
    assert (await client.post(f"{BASE}/architecture/distill", json={})).status_code == 202
