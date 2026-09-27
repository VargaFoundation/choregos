"""La revue croisée est un mécanisme, pas une intention : le backend change vraiment."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _set_cross_backend(project_id: str, value: bool) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        model.review.cross_backend = value
        row.json_doc = model.model_dump(mode="json", exclude_none=True)


async def _implemented_by(setup: Fixture, backend: str) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            Run(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                attempt=1,
                backend=backend,
                status="succeeded",
            )
        )


def _plan(setup: Fixture, role: str, backend: str | None = None) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": f"t-{role}",
        "role": role,
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "checker",
        "attempt": 1,
        "backend": backend,
        "model_request": "profile:by_size",
    }


async def test_le_relecteur_n_est_pas_l_implementeur(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    backend = prepared["stage_input"]["agent"]["backend"]
    assert backend != "codex", "la politique exige un autre relecteur"
    assert backend in {"claude-code", "codex", "gemini-cli", "goose", "opencode", "copilot-cli"}


async def test_sans_la_politique_le_backend_demande_est_respecte(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, False)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"


async def test_un_relecteur_deja_different_n_est_pas_change(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="goose"))

    assert prepared["stage_input"]["agent"]["backend"] == "goose"


async def test_les_autres_roles_ne_sont_pas_touches(setup: Fixture) -> None:
    """Seule la revue est croisée : l'implémentation garde le backend du projet."""
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")

    prepared = await stage_activities.prepare_stage(_plan(setup, "implement", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"


async def test_un_seul_backend_autorise_degrade_sans_bloquer(setup: Fixture) -> None:
    """Un projet qui n'autorise qu'un backend ne doit pas voir ses tickets se bloquer."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "codex")
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        config = dict(project.config)
        config["agent"] = {"default_backend": "codex", "allowed_backends": ["codex"]}
        project.config = config

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="codex"))

    assert prepared["stage_input"]["agent"]["backend"] == "codex"
    assert prepared["run_id"], "le ticket avance malgré la revue dégradée"


# ──────── on n'annonce à l'agent que les serveurs MCP qui existent (2026-09-27) ────────


async def test_le_serveur_d_outils_est_annonce_a_tout_agent() -> None:
    """L'adresse est locale, et quelqu'un la sert TOUJOURS — sidecar sous Tekton, runner ailleurs.

    J'ai brièvement rendu cette annonce conditionnelle, en croyant qu'aucun serveur n'existait
    sous `k8s_job`. La condition retirait une annonce vraie et privait l'agent de ses outils :
    `runner/outils_locaux.py` démarre le serveur quand rien ne répond, mais l'agent ne le sait que
    si on le lui dit.
    """
    from choregos_orchestrator.activities.stage import _serveurs_mcp
    from choregos_orchestrator.config import OrchestratorSettings

    for executeur in ("tekton", "k8s_job", "local_docker", "aca"):
        serveurs = _serveurs_mcp(
            OrchestratorSettings(executor_kind=executeur, memory_url="http://ecphoria:8432")
        )
        assert serveurs["choregos"].url == "http://localhost:7777/mcp", executeur


async def test_l_url_de_la_memoire_vient_de_son_reglage_pas_d_un_remplacement_de_port() -> None:
    """Elle était fabriquée par `gateway_url.replace("4000", "8432")`.

    Le remplacement ne tient que si la passerelle et la mémoire partagent un hôte. Sur le
    locataire dev, la passerelle est `http://litellm:4000` : la mémoire devenait
    `http://litellm:8432` — mesuré sans réponse en huit secondes, quand `http://ecphoria:8432/mcp`
    répond 401 en neuf millisecondes.
    """
    from choregos_orchestrator.activities.stage import _serveurs_mcp
    from choregos_orchestrator.config import OrchestratorSettings

    reglages = OrchestratorSettings(
        executor_kind="k8s_job", gateway_url="http://litellm:4000", memory_url="http://ecphoria:8432"
    )
    serveurs = _serveurs_mcp(reglages)
    assert serveurs["memory"].url == "http://ecphoria:8432/mcp", serveurs["memory"].url
    assert "litellm" not in serveurs["memory"].url, "la mémoire ne vit pas sur l'hôte de la passerelle"

    # Sans mémoire configurée, on n'annonce pas un serveur de mémoire.
    assert "memory" not in _serveurs_mcp(OrchestratorSettings(executor_kind="k8s_job", memory_url=""))
