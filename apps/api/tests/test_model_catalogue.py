"""The organisation's model catalogue (S39-07b, ADR 0060): sync from the gateway, curate, enforce."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core import GatewayModel
from httpx import AsyncClient

from .conftest import login

BASE = "/api/v1/orgs/varga/models"
MODELS = [
    GatewayModel(
        model_name="platform/standard", litellm_model="anthropic/claude-sonnet-5", provider="anthropic"
    ),
    GatewayModel(model_name="platform/strong", litellm_model="anthropic/claude-opus-5", provider="anthropic"),
]
SPEC: dict[str, Any] = {"instructions": "Do the work.", "model": "profile:standard"}


class Scripted:
    """A gateway that lists what the test says; a different type from LiteLLM on purpose."""

    capabilities = frozenset({"list_models"})

    def __init__(self, models: list[GatewayModel]) -> None:
        self.models = models

    async def list_models(self) -> list[GatewayModel]:
        return list(self.models)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Scripted:
    scripted = Scripted(list(MODELS))

    async def org_gateway(session: Any, org_id: str) -> Scripted:
        return scripted

    async def with_type(session: Any, org_id: str) -> tuple[str, Scripted]:
        return "scripted", scripted

    monkeypatch.setattr("choregos_api.routers.model_catalogue.org_gateway", org_gateway)
    monkeypatch.setattr("choregos_api.routers.model_catalogue.org_gateway_and_type", with_type)
    return scripted


async def _entries(client: AsyncClient) -> dict[str, dict[str, Any]]:
    return {e["model_name"]: e for e in (await client.get(BASE)).json()["entries"]}


async def test_the_catalogue_names_the_gateway_and_what_it_can_do(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    body = (await client.get(BASE)).json()
    assert body["mode"] == "open"
    assert body["gateway"] == {"type": "scripted", "capabilities": ["list_models"]}
    assert body["entries"] == []


async def test_sync_enables_new_models_in_open_mode_and_disables_them_in_curated(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    synced = await client.post(f"{BASE}/sync")
    assert synced.json() == {"added": 2, "missing": 0, "seen": 2}
    assert {e["enabled"] for e in (await _entries(client)).values()} == {True}

    assert (await client.put(f"{BASE}/settings", json={"mode": "curated"})).status_code == 200
    gateway.models.append(GatewayModel(model_name="acme/new", litellm_model="acme/new"))
    await client.post(f"{BASE}/sync")
    entries = await _entries(client)
    assert entries["acme/new"]["enabled"] is False, "a model discovered under curated mode arrives disabled"
    assert entries["platform/standard"]["enabled"] is True, "what was decided is kept"


async def test_the_catalogue_exposes_the_upstream_model_and_context_window_of_each_entry(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    gateway.models[:] = [
        GatewayModel(
            model_name="platform/standard",
            litellm_model="anthropic/claude-sonnet-5-20261001",
            provider="anthropic",
            max_input_tokens=200000,
            supports_vision=True,
        )
    ]
    await client.post(f"{BASE}/sync")
    facts = (await _entries(client))["platform/standard"]["gateway"]
    assert facts["upstream_model"] == "anthropic/claude-sonnet-5-20261001"
    assert facts["max_input_tokens"] == 200000
    assert (facts["provider"], facts["supports_vision"]) == ("anthropic", True)


async def test_a_model_gone_from_the_gateway_is_marked_and_keeps_its_settings(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entry = (await _entries(client))["platform/strong"]
    await client.patch(f"{BASE}/{entry['id']}", json={"allowed_roles": ["review"], "notes": "keep"})
    gateway.models.pop()
    assert (await client.post(f"{BASE}/sync")).json()["missing"] == 1
    gone = (await _entries(client))["platform/strong"]
    assert gone["missing_since"] is not None
    assert (gone["allowed_roles"], gone["notes"]) == (["review"], "keep")
    gateway.models.append(MODELS[1])
    await client.post(f"{BASE}/sync")
    assert (await _entries(client))["platform/strong"]["missing_since"] is None


async def test_a_manual_entry_can_be_added_and_deleted_a_gateway_entry_cannot(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    added = await client.post(
        f"{BASE}",
        json={"model_name": "wild/*-chat", "price_override": {"input_per_1k": 0.1, "output_per_1k": 0.2}},
    )
    assert added.status_code == 201, added.text
    assert added.json()["source"] == "manual" and added.json()["price_override"]["output_per_1k"] == 0.2
    assert (await client.post(f"{BASE}", json={"model_name": "wild/*-chat"})).status_code == 409
    assert (await client.delete(f"{BASE}/{added.json()['id']}")).status_code == 204
    from_gateway = (await _entries(client))["platform/standard"]
    assert (await client.delete(f"{BASE}/{from_gateway['id']}")).status_code == 409


async def test_only_an_org_admin_changes_the_catalogue_and_every_change_is_audited(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entry = (await _entries(client))["platform/standard"]
    owner = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert owner.status_code in {200, 201}
    patched = await client.patch(f"{BASE}/{entry['id']}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    await client.put(f"{BASE}/settings", json={"mode": "curated"})
    audit = (await client.get("/api/v1/audit")).json()["items"]
    actions = {row["action"] for row in audit}
    assert {"models.catalogue.sync", "models.catalogue.update", "models.catalogue.settings"} <= actions

    await login(client, "po@varga.dev")
    assert (await client.get(BASE)).status_code == 200, "every member reads"
    assert (await client.patch(f"{BASE}/{entry['id']}", json={"enabled": True})).status_code == 403
    assert (await client.post(f"{BASE}/sync")).status_code == 403
    assert (await client.put(f"{BASE}/settings", json={"mode": "open"})).status_code == 403


async def test_publishing_an_agent_on_a_disabled_model_is_refused_naming_it(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entry = (await _entries(client))["platform/standard"]
    await client.patch(f"{BASE}/{entry['id']}", json={"enabled": False})
    create = {"slug": "worker", "display_name": "Worker", "spec": SPEC}
    refused = await client.post("/api/v1/orgs/varga/agents", json=create)
    assert refused.status_code == 422, refused.text
    assert "platform/standard" in refused.text and "disabled" in refused.text
    # a model that is enabled passes, and so does a new version after re-enabling
    await client.patch(f"{BASE}/{entry['id']}", json={"enabled": True})
    assert (await client.post("/api/v1/orgs/varga/agents", json=create)).status_code == 201
    await client.patch(f"{BASE}/{entry['id']}", json={"enabled": False})
    version = await client.post("/api/v1/orgs/varga/agents/worker/versions", json=SPEC)
    assert version.status_code == 422, version.text


async def test_a_profile_is_checked_through_the_tier_it_resolves_to(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    await client.post(f"{BASE}", json={"model_name": "acme/tiered"})
    entries = await _entries(client)
    assert (
        await client.put(f"{BASE}/settings", json={"mode": "open", "tiers": {"standard": "acme/tiered"}})
    ).status_code == 200
    await client.patch(f"{BASE}/{entries['acme/tiered']['id']}", json={"enabled": False})
    refused = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "tiered", "display_name": "T", "spec": SPEC}
    )
    assert refused.status_code == 422 and "acme/tiered" in refused.text


async def test_a_trial_on_a_disabled_model_is_refused(
    client: AsyncClient, project: dict[str, Any], gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entry = (await _entries(client))["platform/standard"]
    await client.patch(f"{BASE}/{entry['id']}", json={"enabled": False})
    trial = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={"project": "billing-api", "spec": SPEC, "input": {"title": "t", "body": "b"}},
    )
    assert trial.status_code == 422, trial.text


async def test_a_project_profile_mapped_to_a_disabled_model_is_refused(
    client: AsyncClient, project: dict[str, Any], gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    await client.post(f"{BASE}", json={"model_name": "acme/off", "enabled": False})
    put = await client.put(
        f"/api/v1/projects/{project['id']}/models",
        json={"profiles": {"standard": {"litellm_model": "acme/off"}}, "allow_unvalidated": True},
    )
    assert put.status_code == 422, put.text
    assert "acme/off" in put.text


async def test_the_curation_preview_lists_what_would_be_refused(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entry = (await _entries(client))["platform/standard"]
    await client.post("/api/v1/orgs/varga/agents", json={"slug": "worker", "display_name": "W", "spec": SPEC})
    await client.patch(f"{BASE}/{entry['id']}", json={"enabled": False})
    preview = (await client.get(f"{BASE}/curate-preview")).json()
    assert preview["refused"] == [{"kind": "agent", "ref": "worker", "model": "platform/standard"}]


async def test_usable_models_follow_the_catalogue_roles_and_usages(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    await client.post(f"{BASE}/sync")
    entries = await _entries(client)
    await client.patch(f"{BASE}/{entries['platform/strong']['id']}", json={"allowed_roles": ["review"]})
    await client.patch(
        f"{BASE}/{entries['platform/standard']['id']}",
        json={"price_override": {"input_per_1k": 0.5, "output_per_1k": 1.5}},
    )

    async def names(**query: str) -> dict[str, Any]:
        found = (await client.get(f"{BASE}/usable", params=query)).json()
        return {m["model_name"]: m for m in found}

    assert set(await names(role="implement")) == {"platform/standard"}
    assert set(await names(role="review")) == {"platform/standard", "platform/strong"}
    assert set(await names(usage="embed")) == set(), "no model is enabled for embedding"
    standard = (await names())["platform/standard"]
    assert (standard["priced_by"], standard["input_cost_per_1k"]) == ("override", 0.5)
    assert standard["tiers"] == ["standard"]
    await client.put(f"{BASE}/settings", json={"mode": "curated"})
    await client.patch(f"{BASE}/{entries['platform/standard']['id']}", json={"enabled": False})
    assert set(await names(role="review")) == {"platform/strong"}


async def test_switching_to_curated_needs_enabled_tier_models(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    refused = await client.put(
        f"{BASE}/settings", json={"mode": "curated", "tiers": {"standard": "platform/standard"}}
    )
    assert refused.status_code == 422, "the tier names a model nobody enabled"
    await client.post(f"{BASE}/sync")
    ok = await client.put(
        f"{BASE}/settings", json={"mode": "curated", "tiers": {"standard": "platform/standard"}}
    )
    assert ok.status_code == 200
    assert (await client.get(BASE)).json()["tiers"]["standard"] == "platform/standard"


# ───────────────────────── the API's own model uses ─────────────────────────


async def test_indexing_is_refused_when_the_embedding_model_lost_its_usage(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.db.models import KnowledgeDocument
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.conversion import conversions
    from choregos_api.knowledge.embeddings import ModelNotAllowedError, embedding_model
    from choregos_api.knowledge.index import index_document
    from sqlalchemy import select

    await client.post(
        "/api/v1/orgs/varga/knowledge", json={"slug": "kb", "name": "KB", "serving_mode": "index"}
    )
    sent = await client.post(
        "/api/v1/orgs/varga/knowledge/kb/documents",
        files={"file": ("a.md", b"# A\n\nTLS 1.3 is required.\n")},
    )
    assert sent.status_code == 202
    await conversions.drain()
    async with session_scope() as session:
        document_id = (await session.execute(select(KnowledgeDocument.id))).scalars().first()
    assert document_id is not None
    assert await index_document("varga", document_id) > 0, "open mode: indexed"

    # the embedding model is in the catalogue and no longer carries the `embed` usage
    await client.post(BASE, json={"model_name": embedding_model(get_settings()), "usages": ["agent"]})
    with pytest.raises(ModelNotAllowedError, match="/admin/models"):
        await index_document("varga", document_id)


async def test_distillation_is_refused_when_its_model_lost_its_usage(
    client: AsyncClient, admin: str, gateway: Scripted
) -> None:
    from choregos_api.knowledge.conversion import conversions

    await client.post("/api/v1/orgs/varga/knowledge", json={"slug": "kb", "name": "KB"})
    await client.post(
        "/api/v1/orgs/varga/knowledge/kb/documents",
        files={"file": ("a.md", b"# A\n\n## B\n\nTLS 1.3 is required.\n")},
    )
    await conversions.drain()
    await client.post(BASE, json={"model_name": "fake/distiller", "usages": ["agent"]})
    await client.post("/api/v1/orgs/varga/knowledge/kb/distill")
    await conversions.drain()
    outcome = (await client.get("/api/v1/orgs/varga/knowledge/kb")).json()["distillation"]
    assert outcome["status"] == "failed" and "/admin/models" in outcome["error"], outcome
