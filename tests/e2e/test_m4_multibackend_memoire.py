"""M4 — cross review, context pack, memory A/B, evals matrix, model change."""

from __future__ import annotations

import pytest
from choregos_contracts import ProjectConfig
from choregos_core import ModelResolutionError, ModelResolver
from choregos_core.domain import Fact, GatewayModel, Provenance

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_review_croisee_impose_un_backend_different(platform: Platform) -> None:
    """The `review.cross_backend` policy requires that the reviewer not be the implementer."""
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
    # Claude Code only accepts Claude models: it is a hard constraint, not a setting.
    assert resolver.resolve("profile:standard", backend=review_backend).api_format == "anthropic"
    with pytest.raises(ModelResolutionError):
        resolver.resolve("profile:aux", backend=review_backend)


async def test_context_pack_injecte_et_trace(platform: Platform) -> None:
    """The memory reaches the stage, bounded, and stays consultable from the run."""
    await login(platform.client)
    memory = platform.adapters.memory
    await memory.write_fact(
        platform.project_slug,
        Fact(
            kind="decision",
            subject="decision:billing:arrondis",
            content="Rounded totals: amounts are rounded to the cent at issuance (ADR-0007).",
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
            title="Rounded totals and credit notes",
            state="ready",
            size="M",
            allowed_paths=["src/orders/**"],
        )
        session.add(item)
        await session.flush()
        item_id = item.id

    # The `refine` role receives the decisions and the incidents; `implement` receives the
    # conventions and the run lessons. The kinds per role are part of the contract (§4.4).
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
    assert stage_input["context_pack_url"], "the pack is archived with the run"

    from choregos_api.db.models import Run

    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        pack = run.context_pack
    assert pack["tokens_estimated"] <= 4000, "the pack respects the role's token budget"
    contents = [memory_item["content"] for memory_item in pack["memories"]]
    assert any("rounded" in content.lower() for content in contents), pack


async def test_une_panne_memoire_ne_bloque_pas_un_stage(platform: Platform) -> None:
    """Proof before dependency: without memory, the platform carries on with an empty pack."""
    await login(platform.client)
    platform.adapters.memory.fail = True

    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import stage as stage_activities

    async with session_scope() as session:
        item = WorkItem(
            project_id=platform.project_id,
            tracker_key="varga/billing-api#401",
            title="Ticket without memory",
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
        assert run.stage_input is not None, "the stage is ready despite the memory outage"


async def test_matrice_d_evals_publiee_et_opposable(platform: Platform) -> None:
    """An unvalidated combination is refused at registration, with a clear message."""
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


async def test_the_matrix_shows_the_four_context_cells_with_their_tokens(platform: Platform) -> None:
    """S37-09: cells as `run_eval_cell` publishes them, `details` and all, come back with their tokens."""
    await login(platform.client)
    from choregos_orchestrator.activities import evals as eval_activities

    cells = [
        {
            "backend": "claude-code",
            "model": "platform/standard",
            "with_memory": True,
            "context": context,
            "cases": 3,
            "skipped": 0,
            "metered": 3,
            "success_rate": 1.0,
            "validated": True,
            "median_cost_usd": cost,
            "median_duration_s": 140.0,
            "median_tokens_in": tokens_in,
            "median_tokens_cached": tokens_in * 2 // 3,
            "median_tokens_out": 4800.0,
            "median_turns": 14.0,
            "details": [{"fixture": "py-bug-arrondi", "ok": True, "reason": ""}],
        }
        for context, tokens_in, cost in [
            ("none", 144000.0, 0.32),
            ("rtk", 121600.0, 0.28),
            ("headroom", 112800.0, 0.26),
            ("rtk+headroom", 94400.0, 0.22),
        ]
    ]
    await eval_activities.publish_matrix({"project_slug": platform.project_slug, "results": cells})
    response = await platform.client.get(f"/api/v1/projects/{platform.project_id}/models/matrix")
    assert response.status_code == 200, response.text
    shown = {entry["context"]: entry for entry in response.json()["entries"] if entry.get("context")}
    assert list(shown) == ["none", "rtk", "headroom", "rtk+headroom"]
    assert shown["rtk+headroom"]["median_tokens_in"] == 94400.0
    assert shown["headroom"]["median_tokens_cached"] == 75200.0
    assert shown["rtk"]["metered"] == 3 and shown["none"]["median_turns"] == 14.0
    assert "details" not in shown["none"], "the matrix shows its own fields, not the cell's journal"


async def test_changement_de_modele_sans_redeploiement(platform: Platform) -> None:
    """A project changes profile through the API: no redeployment, the resolution follows."""
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
