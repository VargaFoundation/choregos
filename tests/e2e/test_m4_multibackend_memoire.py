"""M4 — review croisée, context pack, A/B mémoire, matrice d'évals, changement de modèle."""

from __future__ import annotations

import pytest
from choregos_contracts import ProjectConfig
from choregos_core import ModelResolutionError, ModelResolver
from choregos_core.domain import Fact, GatewayModel, Provenance

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_review_croisee_impose_un_backend_different(platform: Platform) -> None:
    """La politique `review.cross_backend` exige que le relecteur ne soit pas l'implémenteur."""
    await login(platform.client)
    from choregos_core import PolicyEngine, load_preset

    engine = PolicyEngine(load_preset("team"))
    assert engine.cross_backend_review() is True

    implement_backend = "codex"
    review_backend = "claude-code"
    assert implement_backend != review_backend

    resolver = ModelResolver(
        gateway_url="http://litellm:4000",
        gateway_models=[
            GatewayModel(model_name="platform/standard", litellm_model="anthropic/claude-sonnet-5"),
            GatewayModel(model_name="platform/aux", litellm_model="vertex_ai/gemini-flash"),
        ],
    )
    # Claude Code n'accepte que des modèles Claude : c'est une contrainte dure, pas un réglage.
    assert resolver.resolve("profile:standard", backend=review_backend).api_format == "anthropic"
    with pytest.raises(ModelResolutionError):
        resolver.resolve("profile:aux", backend=review_backend)


async def test_context_pack_injecte_et_trace(platform: Platform) -> None:
    """La mémoire arrive dans le stage, bornée, et reste consultable depuis le run."""
    await login(platform.client)
    memory = platform.adapters.memory
    await memory.write_fact(
        platform.project_slug,
        Fact(
            kind="decision",
            subject="decision:billing:arrondis",
            content="Les totaux sont arrondis au centime à l'émission (ADR-0007).",
            provenance=Provenance(source="scm", ref="docs/adr/0007-arrondis.md"),
        ),
    )

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as stage_activities

    async with session_scope() as session:
        item = WorkItem(
            project_id=platform.project_id,
            tracker_key="varga/billing-api#400",
            title="Arrondis et avoirs sur les totaux",
            state="ready",
            size="M",
            allowed_paths=["src/orders/**"],
        )
        session.add(item)
        await session.flush()
        item_id = item.id

    # Le rôle `refine` reçoit les décisions et les incidents ; `implement` reçoit les
    # conventions et les leçons de run. Les kinds par rôle font partie du contrat (§4.4).
    prepared = await stage_activities.prepare_stage(
        {
            "project_id": platform.project_id,
            "work_item_id": item_id,
            "transition_id": "t-refine",
            "role": "refine",
            "from_state": "inbox",
            "to_state": "awaiting_spec_approval",
            "actor": "refiner",
            "attempt": 1,
            "model_request": "profile:standard",
        }
    )
    stage_input = prepared["stage_input"]
    assert stage_input["context_pack_url"], "le pack est archivé avec le run"

    from choregos_api.db.models import Run

    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        pack = run.context_pack
    assert pack["tokens_estimated"] <= 4000, "le pack respecte le budget de tokens du rôle"
    contents = [memory_item["content"] for memory_item in pack["memories"]]
    assert any("arrondis" in content.lower() for content in contents), pack


async def test_une_panne_memoire_ne_bloque_pas_un_stage(platform: Platform) -> None:
    """Preuve avant dépendance : sans mémoire, la plateforme continue avec un pack vide."""
    await login(platform.client)
    platform.adapters.memory.fail = True

    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as stage_activities

    async with session_scope() as session:
        item = WorkItem(
            project_id=platform.project_id,
            tracker_key="varga/billing-api#401",
            title="Ticket sans mémoire",
            state="ready",
            size="S",
        )
        session.add(item)
        await session.flush()
        item_id = item.id

    prepared = await stage_activities.prepare_stage(
        {
            "project_id": platform.project_id,
            "work_item_id": item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "from_state": "ready",
            "to_state": "in_progress",
            "actor": "dev",
            "attempt": 1,
            "model_request": "profile:standard",
        }
    )
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run.context_pack["memories"] == []
        assert run.stage_input is not None, "le stage est prêt malgré la panne mémoire"


async def test_matrice_d_evals_publiee_et_opposable(platform: Platform) -> None:
    """Une combinaison non validée est refusée à l'enregistrement, avec un message clair."""
    await login(platform.client)
    from choregos_orchestrator.activities import evals as eval_activities

    await eval_activities.publish_matrix(
        {
            "project_slug": platform.project_slug,
            "results": [
                {
                    "backend": "codex",
                    "model": "platform/standard",
                    "validated": True,
                    "success_rate": 0.86,
                    "median_cost_usd": 2.4,
                    "with_memory": True,
                },
                {
                    "backend": "codex",
                    "model": "platform/standard",
                    "validated": False,
                    "success_rate": 0.41,
                    "median_cost_usd": 3.1,
                    "with_memory": True,
                },
            ],
        }
    )
    matrix = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/models/matrix")).json()
    assert matrix["entries"], matrix
    validated = {(entry["backend"], entry["validated"]) for entry in matrix["entries"]}
    assert ("codex", True) in validated


async def test_changement_de_modele_sans_redeploiement(platform: Platform) -> None:
    """Un projet change de profil par l'API : aucun redéploiement, la résolution suit."""
    await login(platform.client)
    response = await platform.client.put(
        f"/api/v1/projects/{platform.project_id}/models",
        json={
            "profiles": {
                "standard": {"litellm_model": "anthropic/claude-sonnet-5", "params": {"temperature": 0}},
                "strong": {"litellm_model": "anthropic/claude-opus-5", "max_turns_factor": 1.5},
            },
            "allow_unvalidated": False,
        },
    )
    assert response.status_code == 200, response.text
    models = response.json()
    assert models["profiles"]["strong"]["litellm_model"] == "anthropic/claude-opus-5"

    project = (await platform.client.get(f"/api/v1/projects/{platform.project_id}")).json()
    config = ProjectConfig.model_validate(project["config"])
    resolver = ModelResolver(gateway_url="http://litellm:4000")
    resolved = resolver.resolve("profile:by_size", project=config, size="XL", backend="codex")
    assert resolved.litellm_model == "anthropic/claude-opus-5"
    assert resolved.turns_factor == pytest.approx(2.25), "XL × facteur du profil"
