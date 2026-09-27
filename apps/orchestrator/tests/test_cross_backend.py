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


async def test_le_sidecar_d_outils_n_est_annonce_que_si_l_executeur_le_monte() -> None:
    """`localhost:7777` n'existe que sous `tekton` : c'est un `sidecar:` de la Task.

    `grep 7777` dans `k8s_job.py` ne rend rien. L'URL était pourtant annoncée à TOUT agent :
    sur le locataire dev, en `k8s_job`, l'agent recevait un serveur d'outils inexistant et rien
    dans le ticket ne disait que le catalogue était hors de portée.
    """
    from choregos_orchestrator.activities.stage import _serveurs_mcp
    from choregos_orchestrator.config import OrchestratorSettings

    tekton = OrchestratorSettings(executor_kind="tekton", memory_url="http://ecphoria:8432")
    assert "choregos" in _serveurs_mcp(tekton), "sous tekton, le sidecar existe"

    k8s_sans_image = OrchestratorSettings(executor_kind="k8s_job", memory_url="http://ecphoria:8432")
    assert "choregos" not in _serveurs_mcp(k8s_sans_image), (
        "sans image d'outils, le Job ne monte aucun sidecar : l'annoncer fait attendre l'agent"
    )

    # Depuis le 2026-09-27, `k8s_job` monte le sidecar lui-même — mais seulement si une image
    # est configurée. Les deux moitiés doivent s'accorder : le Job qui monte, l'URL qu'on annonce.
    k8s_avec_image = OrchestratorSettings(
        executor_kind="k8s_job", memory_url="http://ecphoria:8432", tools_image="reg/outils:1"
    )
    assert _serveurs_mcp(k8s_avec_image)["choregos"].url == "http://localhost:7777/mcp"


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
